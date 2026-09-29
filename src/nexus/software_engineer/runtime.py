"""Single-run protection and recovery for the daily cycle.

One state root, one cycle at a time. A cycle takes an exclusive lease file before it
observes anything and releases it on every exit path. A lease left behind by a cycle
that died (a killed runner, a crashed process) expires after the cycle's runtime budget
plus a grace period, or as soon as its process is gone; the next cycle then records the
interruption as an incident with an audit marker and proceeds. A live lease is never
stolen: the second cycle refuses with ``concurrent_run`` and writes nothing.

A lease file appears with its whole content in one step, so no reader sees it half
written; an unreadable lease is stale only once it is older than a lease can live.
Recovery moves the stale lease aside and checks that it moved the one it judged stale, so
two cycles recovering at once cannot delete each other's fresh lease.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

LEASE_FILENAME = "run.lock"
GRACE_SECONDS = 300


class RunLeaseError(RuntimeError):
    """Another cycle holds the lease, or the lease file cannot be managed."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        super().__init__(code if detail is None else f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class InterruptedRun:
    """A previous cycle that never released its lease and wrote no record."""

    cycle_id: UUID
    started_at: datetime
    expired_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class RunLease:
    path: Path
    cycle_id: UUID
    started_at: datetime
    expires_at: datetime
    pid: int

    def payload(self) -> str:
        return json.dumps(
            {
                "cycle_id": str(self.cycle_id),
                "started_at": self.started_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
                "pid": self.pid,
            },
            sort_keys=True,
        )


def acquire_run_lease(
    state_root: Path,
    *,
    cycle_id: UUID,
    now: datetime,
    max_runtime_seconds: int,
    pid: int | None = None,
    process_alive: object = None,
) -> tuple[RunLease, InterruptedRun | None]:
    """Take the lease or refuse. Returns the lease and any interrupted run it replaced."""

    alive = process_alive if callable(process_alive) else _process_alive
    path = state_root / LEASE_FILENAME
    lease = RunLease(
        path=path,
        cycle_id=cycle_id,
        started_at=now,
        expires_at=now + timedelta(seconds=max_runtime_seconds + GRACE_SECONDS),
        pid=os.getpid() if pid is None else pid,
    )
    try:
        state_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RunLeaseError("lease_unavailable", type(exc).__name__) from exc
    interrupted: InterruptedRun | None = None
    if not _create_exclusive(path, lease.payload()):
        previous = _read(path)
        if previous is None:
            if not _older_than(path, now, lease.expires_at - now):
                raise RunLeaseError(
                    "concurrent_run", "an unreadable lease is too recent to be stale"
                )
            reason = "unreadable lease"
        elif previous.expires_at > now and alive(previous.pid):
            raise RunLeaseError("concurrent_run", f"cycle {previous.cycle_id} holds the lease")
        elif previous.expires_at > now:
            reason = "process gone before its lease expired"
        else:
            reason = "lease expired without a record"
        _retire(path, previous, recovered_by=cycle_id)
        if previous is not None:
            interrupted = InterruptedRun(
                cycle_id=previous.cycle_id,
                started_at=previous.started_at,
                expired_at=previous.expires_at,
                reason=reason,
            )
            _write_marker(state_root, interrupted, recovered_by=cycle_id, now=now)
        if not _create_exclusive(path, lease.payload()):
            raise RunLeaseError("concurrent_run", "the lease was taken while recovering")
    return lease, interrupted


def release_run_lease(lease: RunLease) -> None:
    """Remove the lease only if it is still ours; never remove another cycle's."""

    current = _read(lease.path)
    if current is None or current.cycle_id != lease.cycle_id:
        return
    try:
        lease.path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RunLeaseError("lease_unavailable", type(exc).__name__) from exc


def _create_exclusive(path: Path, payload: str) -> bool:
    """Create ``path`` with its whole content, or return ``False`` if it exists.

    The payload is written to a private file and hard-linked into place, which fails if
    the path exists. Where hard links are unavailable, an exclusive create is the fallback.
    """

    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        _write_new(temporary, payload)
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        except OSError:
            try:
                _write_new(path, payload)
            except FileExistsError:
                return False
        return True
    except OSError as exc:
        raise RunLeaseError("lease_unavailable", type(exc).__name__) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _write_new(path: Path, payload: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")


def _older_than(path: Path, now: datetime, age: timedelta) -> bool:
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise RunLeaseError("lease_unavailable", type(exc).__name__) from exc
    return now - modified > age


def _retire(path: Path, stale: RunLease | None, *, recovered_by: UUID) -> None:
    """Move the stale lease aside, then prove it was the stale one.

    If another cycle recovered first and already holds a fresh lease, the rename moved
    that lease instead: it is put back and this cycle refuses.
    """

    aside = path.with_name(f"{path.name}.retired-{recovered_by.hex}")
    try:
        os.rename(path, aside)
    except FileNotFoundError:
        return  # already retired by another cycle; the exclusive create decides
    except OSError as exc:
        raise RunLeaseError("lease_unavailable", type(exc).__name__) from exc
    moved = _read(aside)
    same = (
        moved is None
        if stale is None
        else moved is not None
        and (moved.cycle_id, moved.started_at) == (stale.cycle_id, stale.started_at)
    )
    if not same:
        try:
            os.link(aside, path)
        except OSError:
            pass
        aside.unlink(missing_ok=True)
        raise RunLeaseError("concurrent_run", "another cycle recovered the lease first")
    aside.unlink(missing_ok=True)


def _read(path: Path) -> RunLease | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RunLease(
            path=path,
            cycle_id=UUID(str(data["cycle_id"])),
            started_at=datetime.fromisoformat(str(data["started_at"])),
            expires_at=datetime.fromisoformat(str(data["expires_at"])),
            pid=int(data["pid"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _write_marker(
    state_root: Path, interrupted: InterruptedRun, *, recovered_by: UUID, now: datetime
) -> None:
    directory = state_root / "cycles"
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / f"{interrupted.cycle_id}.interrupted.json"
    marker.write_text(
        json.dumps(
            {
                "cycle_id": str(interrupted.cycle_id),
                "started_at": interrupted.started_at.isoformat(),
                "lease_expired_at": interrupted.expired_at.isoformat(),
                "reason": interrupted.reason,
                "recovered_by": str(recovered_by),
                "recovered_at": now.astimezone(UTC).isoformat(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


__all__ = [
    "GRACE_SECONDS",
    "LEASE_FILENAME",
    "InterruptedRun",
    "RunLease",
    "RunLeaseError",
    "acquire_run_lease",
    "release_run_lease",
]
