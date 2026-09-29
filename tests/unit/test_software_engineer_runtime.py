"""One cycle at a time per state root; interrupted cycles are recovered, never ignored."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from nexus.patchforge.e2e import FixtureRepository, materialize_fixture
from nexus.patchforge.workspace import GitRunner
from nexus.software_engineer.config import SoftwareEngineerSettings
from nexus.software_engineer.cycle import EngineeringCycle, RunLeaseError
from nexus.software_engineer.inspect import CandidateGenerator, RepositoryInspector
from nexus.software_engineer.memory import (
    EngineerMemory,
    EngineerMemoryStore,
    MemoryCategory,
    MemoryQuery,
)
from nexus.software_engineer.models import (
    CycleDecision,
    CycleMode,
    EngineeringCandidate,
    EngineeringSignal,
)
from nexus.software_engineer.notify import Notifier, RecordingTransport
from nexus.software_engineer.runtime import (
    GRACE_SECONDS,
    LEASE_FILENAME,
    RunLease,
    acquire_run_lease,
    release_run_lease,
)

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
FIXTURE = FixtureRepository(name="lease-fixture", files={"README.md": "# fixture\n"})


class _NoCandidates(CandidateGenerator):
    def generate(
        self, signals: Sequence[EngineeringSignal], memories: Sequence[EngineerMemory] = ()
    ) -> list[EngineeringCandidate]:
        return []


def _cycle(tmp_path: Path, cycle_id: UUID, *, now: datetime = NOW) -> EngineeringCycle:
    git = GitRunner(tmp_path / "git")
    repo = tmp_path / "repo"
    if not repo.exists():
        materialize_fixture(FIXTURE, repo, git, NOW)
    state_root = tmp_path / "state"
    settings = SoftwareEngineerSettings(  # type: ignore[call-arg]
        _env_file=None,
        enabled=True,
        mode=CycleMode.DRY_RUN,
        state_root=state_root,
        memory_path=state_root / "memory.sqlite3",
    )
    return EngineeringCycle(
        settings=settings,
        inspector=RepositoryInspector(repo, git=git, clock=lambda: now),
        memory=EngineerMemoryStore(settings.memory_path),
        notifier=Notifier(RecordingTransport(), clock=lambda: now),
        generator=_NoCandidates(),
        clock=lambda: now,
        cycle_id=cycle_id,
    )


def test_lease_is_exclusive_while_alive_and_released_afterwards(tmp_path: Path) -> None:
    state = tmp_path / "state"
    lease, interrupted = acquire_run_lease(
        state, cycle_id=UUID(int=1), now=NOW, max_runtime_seconds=600
    )
    assert interrupted is None and (state / LEASE_FILENAME).exists()
    assert lease.expires_at == NOW + timedelta(seconds=600 + GRACE_SECONDS)
    with pytest.raises(RunLeaseError, match="concurrent_run") as failure:
        acquire_run_lease(state, cycle_id=UUID(int=2), now=NOW, max_runtime_seconds=600)
    assert failure.value.code == "concurrent_run"
    payload = json.loads((state / LEASE_FILENAME).read_text())
    assert payload["cycle_id"] == str(UUID(int=1)) and payload["pid"] == os.getpid()
    release_run_lease(lease)
    assert not (state / LEASE_FILENAME).exists()
    # Releasing a lease we no longer hold never removes someone else's.
    other, _ = acquire_run_lease(state, cycle_id=UUID(int=3), now=NOW, max_runtime_seconds=600)
    release_run_lease(lease)
    assert (state / LEASE_FILENAME).exists()
    release_run_lease(other)


def test_stale_and_orphaned_leases_are_recovered_with_an_audit_marker(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first, _ = acquire_run_lease(state, cycle_id=UUID(int=1), now=NOW, max_runtime_seconds=60)
    later = first.expires_at + timedelta(seconds=1)
    lease, interrupted = acquire_run_lease(
        state, cycle_id=UUID(int=2), now=later, max_runtime_seconds=60
    )
    assert interrupted is not None and interrupted.cycle_id == UUID(int=1)
    assert interrupted.reason == "lease expired without a record"
    marker = json.loads((state / "cycles" / f"{UUID(int=1)}.interrupted.json").read_text())
    assert marker["recovered_by"] == str(UUID(int=2)) and marker["reason"] == interrupted.reason
    release_run_lease(lease)
    # A lease whose process is gone is recovered even before it expires.
    orphan, _ = acquire_run_lease(
        state, cycle_id=UUID(int=4), now=NOW, max_runtime_seconds=600, pid=2**22 + 7
    )
    lease, interrupted = acquire_run_lease(
        state,
        cycle_id=UUID(int=5),
        now=NOW + timedelta(seconds=5),
        max_runtime_seconds=600,
        process_alive=lambda pid: False,
    )
    assert interrupted is not None and "process gone" in interrupted.reason
    assert lease.cycle_id == UUID(int=5) and orphan.cycle_id == UUID(int=4)
    release_run_lease(lease)
    # An unreadable lease is stale only once it is older than a lease can live, and even
    # then no cycle is invented to blame.
    (state / LEASE_FILENAME).write_text("not json")
    old = (NOW - timedelta(seconds=60 + GRACE_SECONDS + 1)).timestamp()
    os.utime(state / LEASE_FILENAME, (old, old))
    lease, interrupted = acquire_run_lease(
        state, cycle_id=UUID(int=6), now=NOW, max_runtime_seconds=60
    )
    assert interrupted is None and lease.cycle_id == UUID(int=6)
    release_run_lease(lease)


def test_a_lease_that_is_still_being_written_is_never_stolen(tmp_path: Path) -> None:
    """A lease file used to be created empty and filled afterwards; a second cycle reading
    it in between treated it as stale, removed it, and ran concurrently."""

    state = tmp_path / "state"
    state.mkdir()
    (state / LEASE_FILENAME).write_text("")
    fresh = NOW.timestamp()
    os.utime(state / LEASE_FILENAME, (fresh, fresh))
    with pytest.raises(RunLeaseError, match="concurrent_run"):
        acquire_run_lease(state, cycle_id=UUID(int=7), now=NOW, max_runtime_seconds=600)
    assert (state / LEASE_FILENAME).read_text() == ""
    # A created lease is complete the moment it exists, and no temporary file is left.
    lease, _ = acquire_run_lease(
        tmp_path / "other", cycle_id=UUID(int=8), now=NOW, max_runtime_seconds=600
    )
    assert json.loads(lease.path.read_text())["cycle_id"] == str(UUID(int=8))
    assert sorted(item.name for item in lease.path.parent.iterdir()) == [LEASE_FILENAME]


def test_two_recoveries_of_one_stale_lease_never_both_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both cycles judge the same lease stale. The first recovers it and holds a fresh
    lease; the second, still acting on its stale reading, must not delete that lease."""

    from nexus.software_engineer import runtime

    state = tmp_path / "state"
    stale, _ = acquire_run_lease(state, cycle_id=UUID(int=1), now=NOW, max_runtime_seconds=60)
    later = stale.expires_at + timedelta(seconds=1)
    first, interrupted = acquire_run_lease(
        state, cycle_id=UUID(int=2), now=later, max_runtime_seconds=60
    )
    assert interrupted is not None and interrupted.cycle_id == UUID(int=1)
    # The second cycle read the lease before the first replaced it.
    original_read = runtime._read
    readings = iter([stale])
    monkeypatch.setattr(runtime, "_read", lambda path: next(readings, None) or original_read(path))
    with pytest.raises(RunLeaseError, match="concurrent_run"):
        acquire_run_lease(state, cycle_id=UUID(int=3), now=later, max_runtime_seconds=60)
    monkeypatch.setattr(runtime, "_read", original_read)
    holder = json.loads((state / LEASE_FILENAME).read_text())
    assert holder["cycle_id"] == str(UUID(int=2)), "the fresh lease was taken"
    assert not list(state.glob(f"{LEASE_FILENAME}.retired-*"))
    release_run_lease(first)


def test_cycle_refuses_a_concurrent_run_and_records_a_recovered_interruption(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    held, _ = acquire_run_lease(state, cycle_id=UUID(int=10), now=NOW, max_runtime_seconds=1800)
    with pytest.raises(RunLeaseError, match="concurrent_run"):
        _cycle(tmp_path, UUID(int=11)).run()
    assert not (state / "cycles").exists(), "a refused cycle writes nothing"
    release_run_lease(held)

    # A dead earlier cycle: the next run recovers it, notes the incident, and proceeds.
    dead = RunLease(
        path=state / LEASE_FILENAME,
        cycle_id=UUID(int=12),
        started_at=NOW - timedelta(hours=3),
        expires_at=NOW - timedelta(hours=2),
        pid=2**22 + 9,
    )
    dead.path.write_text(dead.payload() + "\n")
    cycle = _cycle(tmp_path, UUID(int=13))
    record, report = cycle.run()
    assert record.decision is CycleDecision.NO_WORK and record.failure is None
    assert not (state / LEASE_FILENAME).exists(), "the lease is released after the run"
    incidents = cycle.memory.retrieve(MemoryQuery(category=MemoryCategory.INCIDENT))
    assert len(incidents) == 1 and str(UUID(int=12)) in incidents[0].content
    assert incidents[0].provenance.cycle_id == UUID(int=13)
    assert (state / "cycles" / f"{UUID(int=12)}.interrupted.json").exists()
    assert "interrupted" in report.text
    # The recovery is remembered once; a further cycle finds a clean lease.
    again, _ = _cycle(tmp_path, UUID(int=14)).run()
    assert again.decision is CycleDecision.NO_WORK
    assert len(cycle.memory.retrieve(MemoryQuery(category=MemoryCategory.INCIDENT))) == 1


def test_lease_is_released_when_the_cycle_fails(tmp_path: Path) -> None:
    cycle = _cycle(tmp_path, UUID(int=20))

    def boom() -> str:
        raise RuntimeError("inspector exploded")

    cycle.inspector.head_sha = boom  # type: ignore[method-assign]
    record, _ = cycle.run()
    assert record.failure is not None
    assert not (tmp_path / "state" / LEASE_FILENAME).exists()
