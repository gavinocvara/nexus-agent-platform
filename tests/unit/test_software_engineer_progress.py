"""Live phase progress: published per transition, atomic, owned, and always cleaned up."""

import json
import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from nexus.patchforge.e2e import FixtureRepository, materialize_fixture
from nexus.patchforge.workspace import GitRunner
from nexus.software_engineer.config import SoftwareEngineerSettings
from nexus.software_engineer.cycle import (
    DryRunExecutor,
    EngineeringCycle,
    ExecutionOutcome,
    ExecutorError,
)
from nexus.software_engineer.executor import PatchForgeExecutor
from nexus.software_engineer.inspect import CandidateGenerator, RepositoryInspector
from nexus.software_engineer.memory import EngineerMemory, EngineerMemoryStore
from nexus.software_engineer.models import (
    CandidateEstimate,
    ChangeCategory,
    ChangeSummary,
    CycleBudget,
    CycleDecision,
    CycleFailure,
    CycleMode,
    CyclePhase,
    EngineeringCandidate,
    EngineeringSignal,
    GateResult,
    GateStatus,
    RollbackRecord,
)
from nexus.software_engineer.notify import Notifier, RecordingTransport
from nexus.software_engineer.policy import REQUIRED_GATES_FOR_AUTONOMOUS_SHIP
from nexus.software_engineer.progress import (
    PROGRESS_FILENAME,
    CycleProgress,
    ProgressPublisher,
    executor_kind,
    progress_path,
    read_progress,
)
from nexus.software_engineer.runtime import LEASE_FILENAME, RunLease, RunLeaseError

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
CYCLE = UUID(int=4242)
OTHER = UUID(int=9999)
FIXTURE = FixtureRepository(
    name="progressed",
    files={"src/pkg/module.py": "VALUE = 1\n", "README.md": "# teh fixture\n"},
)
PATCH = b"--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-# teh fixture\n+# the fixture\n"
CONTRACT_KEYS = {
    "schema_version",
    "cycle_id",
    "sequence",
    "phase",
    "executor",
    "started_at",
    "updated_at",
    "attested_by",
}


class ValidatingExecutor:
    """Produces a validated documentation change, or raises when told to."""

    can_ship = True

    def __init__(self, *, error: str | None = None) -> None:
        self.error = error

    def execute(
        self, candidate: EngineeringCandidate, *, cycle_id: UUID, budget: CycleBudget
    ) -> ExecutionOutcome:
        if self.error is not None:
            raise ExecutorError(self.error)
        change = ChangeSummary(
            base_sha="1" * 40,
            branch="nexus/software-engineer/candidate",
            commit_sha="2" * 40,
            changed_files=["README.md"],
            additions=1,
            deletions=1,
            diff_bytes=len(PATCH),
            diff_sha256=sha256(PATCH).hexdigest(),
            rollback_reference="git revert 2222222",
        )
        gates = [
            GateResult(
                gate=gate,
                status=GateStatus.PASSED,
                summary="scripted",
                evidence_sha256=sha256(gate.value.encode()).hexdigest(),
            )
            for gate in sorted(REQUIRED_GATES_FOR_AUTONOMOUS_SHIP, key=lambda item: item.value)
        ]
        return ExecutionOutcome(change=change, patch=PATCH, gates=gates, root_cause_evidence=True)

    def ship(self, change: ChangeSummary, *, cycle_id: UUID) -> ChangeSummary:
        return change

    def rollback(self, change: ChangeSummary, *, reason: str) -> RollbackRecord:
        return RollbackRecord(
            reverted_commit_sha="2" * 40,
            revert_reference="git revert",
            reason=reason,
            occurred_at=NOW,
        )


class ReadmeCandidate(CandidateGenerator):
    def generate(
        self, signals: Sequence[EngineeringSignal], memories: Sequence[EngineerMemory] = ()
    ) -> list[EngineeringCandidate]:
        return [
            EngineeringCandidate(
                candidate_id=UUID(int=77),
                title="Fix README typo",
                rationale="A one-word documentation correction.",
                category=ChangeCategory.DOCUMENTATION_CORRECTION,
                expected_paths=["README.md"],
                signal_ids=[signals[0].signal_id],
                estimate=CandidateEstimate(value=70, urgency=40, confidence=90, cost=10),
            )
        ]


def _cycle(
    tmp_path: Path,
    mode: CycleMode,
    *,
    executor: ValidatingExecutor | None = None,
    candidate: bool = True,
    clock: Callable[[], datetime] | None = None,
    cycle_id: UUID = CYCLE,
    **overrides: object,
) -> EngineeringCycle:
    git = GitRunner(tmp_path / "git")
    repo = tmp_path / "repo"
    if not repo.exists():
        materialize_fixture(FIXTURE, repo, git, NOW)
    settings = SoftwareEngineerSettings.model_validate(
        {
            "enabled": True,
            "mode": mode,
            "state_root": tmp_path / "state",
            "memory_path": tmp_path / "state" / "memory.sqlite3",
            **overrides,
        }
    )
    tick = clock or (lambda: NOW)
    return EngineeringCycle(
        settings=settings,
        inspector=RepositoryInspector(repo, git=git, clock=tick),
        memory=EngineerMemoryStore(settings.memory_path),
        notifier=Notifier(RecordingTransport(), clock=tick),
        executor=executor,
        generator=ReadmeCandidate() if candidate else None,
        clock=tick,
        cycle_id=cycle_id,
    )


def _observe(monkeypatch: pytest.MonkeyPatch, state_root: Path) -> list[CycleProgress]:
    """Record what an observer could read right after every publish."""

    seen: list[CycleProgress] = []
    original = ProgressPublisher.publish

    def spy(self: ProgressPublisher, **kwargs: object) -> None:
        original(self, **kwargs)  # type: ignore[arg-type]
        current = read_progress(state_root)
        assert current is not None, "a published phase must be readable immediately"
        raw = json.loads(progress_path(state_root).read_text(encoding="utf-8"))
        assert set(raw) == CONTRACT_KEYS, "the file carries the minimal contract only"
        leftovers = list(progress_path(state_root).parent.glob(f".{PROGRESS_FILENAME}.*.tmp"))
        assert not leftovers, "no temporary file survives a publish"
        seen.append(current)

    monkeypatch.setattr(ProgressPublisher, "publish", spy)
    return seen


def _lease(state_root: Path, cycle_id: UUID, *, expires: datetime, pid: int) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / LEASE_FILENAME).write_text(
        json.dumps(
            {
                "cycle_id": str(cycle_id),
                "started_at": (expires - timedelta(hours=1)).isoformat(),
                "expires_at": expires.isoformat(),
                "pid": pid,
            }
        ),
        encoding="utf-8",
    )


def _foreign_progress(state_root: Path, cycle_id: UUID = OTHER) -> bytes:
    record = CycleProgress(
        cycle_id=cycle_id,
        sequence=6,
        phase=CyclePhase.IMPLEMENT,
        executor="patchforge",
        started_at=NOW - timedelta(hours=2),
        updated_at=NOW - timedelta(hours=2),
    )
    path = progress_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (record.model_dump_json() + "\n").encode()
    path.write_bytes(payload)
    return payload


def test_every_runtime_transition_is_published_in_order_then_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    seen = _observe(monkeypatch, state_root)
    cycle = _cycle(tmp_path, CycleMode.AUTONOMOUS_LOW_RISK, executor=ValidatingExecutor())
    record, _ = cycle.run()
    assert record.decision is CycleDecision.SHIP
    assert [item.sequence for item in seen] == [item.sequence for item in record.transitions]
    assert [item.phase for item in seen] == [item.target for item in record.transitions]
    assert [item.phase for item in seen] == [
        CyclePhase.OBSERVE,
        CyclePhase.UNDERSTAND,
        CyclePhase.PRIORITIZE,
        CyclePhase.INVESTIGATE,
        CyclePhase.PLAN,
        CyclePhase.IMPLEMENT,
        CyclePhase.TEST,
        CyclePhase.SELF_REVIEW,
        CyclePhase.ASSESS_RISK,
        CyclePhase.DECIDE,
        CyclePhase.OBSERVE_RESULTS,
        CyclePhase.LEARN,
        CyclePhase.REPORT,
        CyclePhase.CLOSED,
    ]
    assert {item.cycle_id for item in seen} == {CYCLE}
    assert {item.executor for item in seen} == {"other"}
    assert all(item.started_at == record.started_at for item in seen)
    assert not progress_path(state_root).exists(), "the finished cycle removes its progress"
    assert not (state_root / LEASE_FILENAME).exists()
    assert (state_root / "cycles" / f"{CYCLE}.json").exists(), "the record landed first"


def test_progress_never_carries_reasons_signals_or_model_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []
    original = ProgressPublisher.publish

    def spy(self: ProgressPublisher, **kwargs: object) -> None:
        original(self, **kwargs)  # type: ignore[arg-type]
        captured.append(progress_path(tmp_path / "state").read_text(encoding="utf-8"))

    monkeypatch.setattr(ProgressPublisher, "publish", spy)
    record, _ = _cycle(tmp_path, CycleMode.PROPOSE, executor=ValidatingExecutor()).run()
    text = "".join(captured)
    for transition in record.transitions:
        assert transition.reason not in text
    for signal in record.signals:
        assert signal.summary not in text
    assert "README" not in text and "typo" not in text


def test_dry_run_is_published_as_a_dry_run_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _observe(monkeypatch, tmp_path / "state")
    record, _ = _cycle(tmp_path, CycleMode.DRY_RUN, executor=ValidatingExecutor()).run()
    assert record.decision is CycleDecision.REQUEST_APPROVAL
    assert {item.executor for item in seen} == {"dry_run"}
    assert not progress_path(tmp_path / "state").exists()


def test_executor_kinds_name_the_real_classes() -> None:
    assert executor_kind(DryRunExecutor()) == "dry_run"
    assert executor_kind(PatchForgeExecutor.__new__(PatchForgeExecutor)) == "patchforge"
    assert executor_kind(ValidatingExecutor()) == "other"


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("executor_error", {"executor": ValidatingExecutor(error="merge conflict")}),
        ("budget_exhausted", {"max_runtime_seconds": 60}),
    ],
)
def test_a_failed_cycle_still_removes_its_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str, kwargs: dict[str, object]
) -> None:
    seen = _observe(monkeypatch, tmp_path / "state")
    clock: Callable[[], datetime] | None = None
    if label == "budget_exhausted":
        ticks = iter(NOW + timedelta(seconds=90 * index) for index in range(200))
        clock = lambda: next(ticks)  # noqa: E731
    record, _ = _cycle(tmp_path, CycleMode.PROPOSE, clock=clock, **kwargs).run()  # type: ignore[arg-type]
    assert record.decision is CycleDecision.BLOCKED
    expected = (
        CycleFailure.EXECUTOR_ERROR if label == "executor_error" else CycleFailure.BUDGET_EXHAUSTED
    )
    assert record.failure is expected
    assert seen and seen[-1].phase is CyclePhase.CLOSED
    assert not progress_path(tmp_path / "state").exists()
    assert not (tmp_path / "state" / LEASE_FILENAME).exists()


def test_a_crash_while_persisting_removes_progress_before_the_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cycle = _cycle(tmp_path, CycleMode.DRY_RUN)
    order: list[str] = []

    def crash(record: object, report: object) -> None:
        assert progress_path(tmp_path / "state").exists(), "progress is live until the end"
        raise OSError("disk full")

    original_finish = ProgressPublisher.finish

    def finish(self: ProgressPublisher) -> None:
        order.append("progress")
        original_finish(self)

    monkeypatch.setattr(cycle, "_persist", crash)
    monkeypatch.setattr(ProgressPublisher, "finish", finish)

    def release(lease: RunLease) -> None:
        order.append("lease")
        lease.path.unlink()

    monkeypatch.setattr("nexus.software_engineer.cycle.release_run_lease", release)
    with pytest.raises(OSError, match="disk full"):
        cycle.run()
    assert order == ["progress", "lease"]
    assert not progress_path(tmp_path / "state").exists()


def test_a_failed_write_keeps_the_last_good_phase_and_never_fails_the_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    snapshots: list[CycleProgress | None] = []
    real_replace = os.replace
    calls = {"count": 0}

    def flaky_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        if str(target).endswith(PROGRESS_FILENAME):
            calls["count"] += 1
            if calls["count"] == 3:
                snapshots.append(read_progress(state_root))
                raise OSError("device busy")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", flaky_replace)
    original_finish = ProgressPublisher.finish

    def finish(self: ProgressPublisher) -> None:
        snapshots.append(read_progress(state_root))
        assert self.failed, "publishing stopped after the failed write"
        assert not list(progress_path(state_root).parent.glob(f".{PROGRESS_FILENAME}.*.tmp"))
        original_finish(self)

    monkeypatch.setattr(ProgressPublisher, "finish", finish)
    record, _ = _cycle(tmp_path, CycleMode.DRY_RUN).run()
    assert record.phase_reached is CyclePhase.CLOSED and record.failure is None
    before, at_finish = snapshots
    assert before is not None and before.phase is CyclePhase.UNDERSTAND
    assert at_finish == before, "the reader kept the last complete phase, never a partial one"
    assert calls["count"] == 3, "no further writes after a failure"
    assert not progress_path(state_root).exists()


def test_a_dead_cycles_progress_is_discarded_when_the_lease_is_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    _lease(state_root, OTHER, expires=NOW - timedelta(minutes=1), pid=999_999)
    _foreign_progress(state_root)
    seen = _observe(monkeypatch, state_root)
    record, _ = _cycle(tmp_path, CycleMode.DRY_RUN).run()
    assert record.phase_reached is CyclePhase.CLOSED
    assert {item.cycle_id for item in seen} == {CYCLE}, "the dead cycle's phase never reappears"
    assert not progress_path(state_root).exists()
    assert (state_root / "cycles" / f"{OTHER}.interrupted.json").exists()


def test_a_refused_concurrent_cycle_leaves_the_live_progress_alone(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    _lease(state_root, OTHER, expires=NOW + timedelta(hours=1), pid=os.getpid())
    payload = _foreign_progress(state_root)
    with pytest.raises(RunLeaseError, match="concurrent_run"):
        _cycle(tmp_path, CycleMode.DRY_RUN).run()
    assert progress_path(state_root).read_bytes() == payload


def test_finish_never_removes_another_cycles_file_and_reader_rejects_garbage(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    payload = _foreign_progress(state_root)
    publisher = ProgressPublisher(state_root, cycle_id=CYCLE, executor="other", started_at=NOW)
    publisher.finish()
    assert progress_path(state_root).read_bytes() == payload
    progress_path(state_root).write_text('{"cycle_id": "not-a-uuid"}', encoding="utf-8")
    assert read_progress(state_root) is None
    progress_path(state_root).write_text("x" * 10_000, encoding="utf-8")
    assert read_progress(state_root) is None
    progress_path(state_root).unlink()
    assert read_progress(state_root) is None


def test_an_invalid_progress_record_stops_publishing_without_failing_the_cycle(
    tmp_path: Path,
) -> None:
    publisher = ProgressPublisher(tmp_path, cycle_id=CYCLE, executor="other", started_at=NOW)
    publisher.publish(sequence=101, phase=CyclePhase.OBSERVE, now=NOW)  # beyond the contract
    assert publisher.failed and not progress_path(tmp_path).exists()
    publisher.publish(sequence=1, phase=CyclePhase.OBSERVE, now=NOW)
    assert not progress_path(tmp_path).exists(), "publishing stays stopped"
