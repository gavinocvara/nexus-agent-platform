"""Command Center event stream: semantic signals, SSE framing, bounded subscribers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from nexus.command_center.config import CommandCenterSettings
from nexus.command_center.models import (
    ActiveRunView,
    CycleSummary,
    DecisionView,
    HealthView,
    Snapshot,
)
from nexus.command_center.snapshot import SnapshotBuilder
from nexus.command_center.stream import (
    QUEUE_SIZE,
    SnapshotHub,
    StreamEvent,
    diff_signals,
    event_stream,
)
from nexus.software_engineer.config import SoftwareEngineerSettings

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def _hub(tmp_path: Path) -> SnapshotHub:
    settings = CommandCenterSettings.model_validate(
        {
            "engineer_state_root": tmp_path / "state",
            "engineer_memory_path": tmp_path / "state" / "memory.sqlite3",
            "brain_path": tmp_path / "brain.sqlite3",
            "scenario_directory": ROOT / "lab" / "scenarios" / "v1",
            "health_enabled": False,
            "replay_enabled": False,
        }
    )
    builder = SnapshotBuilder(settings, SoftwareEngineerSettings.model_validate({}))
    return SnapshotHub(
        builder, max_subscribers=2, poll_interval=1, health_interval=15, health_reader=None
    )


def _base(tmp_path: Path) -> Snapshot:
    return asyncio.run(_hub(tmp_path).current())


def _summary(cycle_id: str, decision: str = "no_work") -> CycleSummary:
    return CycleSummary(
        cycle_id=cycle_id,
        mode="dry_run",
        started_at=NOW,
        completed_at=NOW,
        phase_reached="closed",
        decision=decision,
        failure=None,
        risk_level=None,
        selected_title=None,
        gates_passed=0,
        gates_failed=0,
        owner_action_required=False,
        published=False,
    )


def test_first_snapshot_has_no_signals(tmp_path: Path) -> None:
    assert diff_signals(None, _base(tmp_path)) == []


def test_run_lifecycle_cycle_decision_and_health_signals(tmp_path: Path) -> None:
    base = _base(tmp_path)
    run = ActiveRunView(cycle_id="c1", started_at=NOW, lease_expires_at=NOW)
    started = base.model_copy(update={"active_run": run})
    assert [item.kind for item in diff_signals(base, started)] == ["run.started"]

    decision = DecisionView(
        decision_id="d1",
        request_id="r1",
        verdict="ship",
        decided_by="human:owner",
        reason="ok",
        channel="slack",
        decided_at=NOW,
    )
    ended = base.model_copy(
        update={
            "cycles": [_summary("c1", "request_approval")],
            "decisions": [decision],
            "health": HealthView(state="ok", overall="healthy", services=[], observed_at=NOW),
        }
    )
    kinds = [item.kind for item in diff_signals(started, ended)]
    assert kinds == ["run.ended", "cycle.recorded", "decision.recorded", "health.changed"]
    assert diff_signals(ended, ended) == []


def test_event_framing_is_single_line_sse() -> None:
    encoded = StreamEvent(7, "signal", '{"kind":"run.started"}').encode()
    assert encoded == b'id: 7\nevent: signal\ndata: {"kind":"run.started"}\n\n'


def test_stream_sends_snapshot_then_events_and_drops_slow_subscribers(tmp_path: Path) -> None:
    async def scenario() -> None:
        hub = _hub(tmp_path)
        await hub.refresh(force=True)
        queue = hub.subscribe()

        async def connected() -> bool:
            return False

        stream = event_stream(hub, queue, connected, heartbeat=0.01)
        assert await anext(stream) == b"retry: 3000\n\n"
        first = await anext(stream)
        assert first.startswith(b"id: ") and b"event: snapshot" in first
        assert await anext(stream) == b": heartbeat\n\n"
        await hub.refresh(force=True)
        assert b"event: snapshot" in await anext(stream)
        await stream.aclose()
        assert queue not in hub._subscribers

        slow = hub.subscribe()
        for _ in range(QUEUE_SIZE + 1):
            await hub.refresh(force=True)
        assert slow not in hub._subscribers

        hub.subscribe()
        hub.subscribe()
        try:
            hub.subscribe()
        except Exception as exc:  # noqa: BLE001
            assert type(exc).__name__ == "TooManySubscribers"
        else:
            raise AssertionError("subscriber bound not enforced")

    asyncio.run(scenario())
