"""Single-run protection and recovery for the daily cycle.

One state root, one cycle at a time. A cycle takes an exclusive lease file before it
observes anything and releases it on every exit path. A lease left behind by a cycle
that died (a killed runner, a crashed process) expires after the cycle's runtime budget
plus a grace period, or as soon as its process is gone; the next cycle then records the
interruption as an incident with an audit marker and proceeds. A live lease is never
stolen: the second cycle refuses with ``concurrent_run`` and writes nothing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

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
            reason = "unreadable lease"
        elif previous.expires_at > now and alive(previous.pid):
            raise RunLeaseError("concurrent_run", f"cycle {previous.cycle_id} holds the lease")
        elif previous.expires_at > now:
            reason = "process gone before its lease expired"
        else:
            reason = "lease expired without a record"
        if previous is not None:
            interrupted = InterruptedRun(
                cycle_id=previous.cycle_id,
                started_at=previous.started_at,
                expired_at=previous.expires_at,
                reason=reason,
            )
            _write_marker(state_root, interrupted, recovered_by=cycle_id, now=now)
        try:
            path.unlink()
        except OSError as exc:
            raise RunLeaseError("lease_unavailable", type(exc).__name__) from exc
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
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    except OSError as exc:
        raise RunLeaseError("lease_unavailable", type(exc).__name__) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    return True


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
