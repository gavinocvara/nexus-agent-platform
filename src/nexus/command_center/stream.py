"""Server-to-client event stream: snapshots on change, semantic signals, heartbeats.

The hub polls cheap file fingerprints and rebuilds the snapshot only when something changed
(at most once per poll interval). Subscribers get bounded queues; a subscriber that cannot
keep up is dropped rather than allowed to grow memory, and reconnects to a fresh snapshot.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from nexus.command_center.models import Signal, Snapshot
from nexus.command_center.snapshot import HealthSample, SnapshotBuilder, utc_now
from nexus.diagnostics.models import SystemHealthResult

HEARTBEAT_SECONDS = 15.0
QUEUE_SIZE = 32


@dataclass(frozen=True, slots=True)
class StreamEvent:
    event_id: int
    kind: str
    data: str

    def encode(self) -> bytes:
        # JSON from pydantic never contains a raw newline, so one ``data:`` line suffices.
        return f"id: {self.event_id}\nevent: {self.kind}\ndata: {self.data}\n\n".encode()


class TooManySubscribers(RuntimeError):
    pass


def diff_signals(previous: Snapshot | None, current: Snapshot) -> list[Signal]:
    """Semantic changes between consecutive snapshots; none for the first snapshot."""

    if previous is None:
        return []
    signals: list[Signal] = []
    before, after = previous.active_run, current.active_run
    if after is not None and (before is None or before.cycle_id != after.cycle_id):
        signals.append(Signal(kind="run.started", subject=after.cycle_id))
    if before is not None and (after is None or after.cycle_id != before.cycle_id):
        signals.append(Signal(kind="run.ended", subject=before.cycle_id))
    known = {item.cycle_id for item in previous.cycles}
    for item in reversed(current.cycles):
        if item.cycle_id not in known:
            signals.append(
                Signal(kind="cycle.recorded", subject=item.cycle_id, detail=item.decision)
            )
    pending_before = previous.pending_approval
    pending_after = current.pending_approval
    if pending_after is not None and (
        pending_before is None or pending_before.request_id != pending_after.request_id
    ):
        signals.append(Signal(kind="decision.pending", subject=pending_after.request_id))
    decided = {item.decision_id for item in previous.decisions}
    for decision in reversed(current.decisions):
        if decision.decision_id not in decided:
            signals.append(
                Signal(
                    kind="decision.recorded", subject=decision.request_id, detail=decision.verdict
                )
            )
    published = {item.pull_request_url for item in previous.publications}
    for publication in reversed(current.publications):
        if publication.pull_request_url not in published:
            signals.append(
                Signal(
                    kind="publication.recorded",
                    subject=str(publication.pull_request_number),
                    detail=publication.pull_request_url,
                )
            )
    if previous.health.overall != current.health.overall:
        signals.append(
            Signal(
                kind="health.changed",
                subject=current.health.overall,
                detail=previous.health.overall,
            )
        )
    return signals


class SnapshotHub:
    def __init__(
        self,
        builder: SnapshotBuilder,
        *,
        max_subscribers: int,
        poll_interval: float,
        health_interval: float,
        health_reader: Callable[[], SystemHealthResult] | None,
    ) -> None:
        self.builder = builder
        self.max_subscribers = max_subscribers
        self.poll_interval = poll_interval
        self.health_interval = health_interval
        self.health_reader = health_reader
        self._subscribers: set[asyncio.Queue[StreamEvent | None]] = set()
        self._snapshot: Snapshot | None = None
        self._fingerprint: object = None
        self._sequence = 0
        self._event_id = 0
        self._refresh_lock = asyncio.Lock()

    @property
    def snapshot(self) -> Snapshot | None:
        return self._snapshot

    async def current(self) -> Snapshot:
        if self._snapshot is None:
            await self.refresh(force=True)
        assert self._snapshot is not None
        return self._snapshot

    async def refresh(self, *, force: bool = False) -> list[Signal]:
        async with self._refresh_lock:
            fingerprint = await asyncio.to_thread(self.builder.fingerprint)
            if not force and self._snapshot is not None and fingerprint == self._fingerprint:
                return []
            snapshot = await asyncio.to_thread(self.builder.build, self._sequence + 1)
            previous = self._snapshot
            self._sequence += 1
            self._snapshot = snapshot
            self._fingerprint = fingerprint
            signals = diff_signals(previous, snapshot)
            self._publish("snapshot", snapshot.model_dump_json())
            for signal in signals:
                self._publish("signal", signal.model_dump_json())
            return signals

    async def poll_forever(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self.refresh()
            await asyncio.sleep(self.poll_interval)

    async def health_forever(self) -> None:
        if self.health_reader is None:
            return
        reader = self.health_reader
        while True:
            try:
                result = await asyncio.to_thread(reader)
            except Exception as exc:  # noqa: BLE001 - a failed read is shown, not raised
                sample = HealthSample(
                    state="unavailable",
                    result=None,
                    observed_at=utc_now(),
                    detail=f"Health read failed: {type(exc).__name__}",
                )
            else:
                sample = HealthSample(
                    state="ok" if result.success else "unavailable",
                    result=result,
                    observed_at=result.observed_at,
                    detail=None if result.success else "One or more lab endpoints unreachable",
                )
            self.builder.set_health(sample)
            await self.refresh()
            await asyncio.sleep(self.health_interval)

    def subscribe(self) -> asyncio.Queue[StreamEvent | None]:
        if len(self._subscribers) >= self.max_subscribers:
            raise TooManySubscribers
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[StreamEvent | None]) -> None:
        self._subscribers.discard(queue)

    def initial_event(self, snapshot: Snapshot) -> StreamEvent:
        return StreamEvent(self._event_id, "snapshot", snapshot.model_dump_json())

    def _publish(self, kind: str, data: str) -> None:
        self._event_id += 1
        event = StreamEvent(self._event_id, kind, data)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Too slow: end its stream; the client reconnects to a fresh snapshot.
                self._subscribers.discard(queue)
                with contextlib.suppress(asyncio.QueueFull):
                    queue.get_nowait()
                    queue.put_nowait(None)


async def event_stream(
    hub: SnapshotHub,
    queue: asyncio.Queue[StreamEvent | None],
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    heartbeat: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[bytes]:
    try:
        yield b"retry: 3000\n\n"
        yield hub.initial_event(await hub.current()).encode()
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except TimeoutError:
                if await is_disconnected():
                    return
                yield b": heartbeat\n\n"
                continue
            if event is None:
                return
            yield event.encode()
    finally:
        hub.unsubscribe(queue)


__all__ = [
    "HEARTBEAT_SECONDS",
    "SnapshotHub",
    "StreamEvent",
    "TooManySubscribers",
    "diff_signals",
    "event_stream",
]
