import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from nexus.aegisops.config import AgentSettings
from nexus.aegisops.models import Diagnosis, DiagnosisStatus
from nexus.aegisops.runtime import EngineOutcome
from nexus.evaluation.aegisops.analytics import summarize
from nexus.evaluation.aegisops.benchmark import BenchmarkRunner
from nexus.evaluation.aegisops.benchmark_models import (
    BaselineIdentity,
    BenchmarkMode,
    BenchmarkRunRecord,
    BenchmarkRunStatus,
    BenchmarkSessionStatus,
    ObservableToolCall,
    OrderingStrategy,
)
from nexus.evaluation.aegisops.environment import EnvironmentIntegrityError
from nexus.evaluation.aegisops.storage import BenchmarkStorage, BenchmarkStorageError
from nexus.lab.catalog import ScenarioCatalog


def _identity(**updates: object) -> BaselineIdentity:
    values = {
        "git_sha": "a" * 40,
        "git_dirty": False,
        "reproducible": True,
        "nexus_version": "0.7.0",
        "model": "scripted-model",
        "instruction_hash": "i" * 64,
        "tool_registry_hash": "t" * 64,
        "diagnosis_schema_hash": "d" * 64,
        "scenario_catalog_hash": "s" * 64,
        "max_turns": 10,
        "max_tool_calls": 12,
        "timeout_seconds": 120,
        "agents_sdk_version": "0.22.3",
        "python_version": "3.12.1",
        "platform": "test",
    }
    values.update(updates)
    return BaselineIdentity.model_validate(values)


def _diagnosis() -> Diagnosis:
    return Diagnosis(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        summary="Scripted abstention.",
        confidence=0.2,
        next_diagnostic_action="Read system health.",
    )


class FakeEngine:
    async def run(self, prompt, context, settings):  # type: ignore[no-untyped-def]
        return EngineOutcome(_diagnosis(), turn_count=1)


class FakeStack:
    def __init__(self, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def clean_restart(self) -> None:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise EnvironmentIntegrityError("scripted stack failure")


class FakeWarmup:
    def __init__(self) -> None:
        self.calls = 0

    def run(self) -> None:
        self.calls += 1


class FakeScenarioRunner:
    def __init__(self, fail_recovery: bool = False) -> None:
        self.events: list[str] = []
        self.fail_recovery = fail_recovery
        self.reset_count = 0

    def reset(self, scenario, client):  # type: ignore[no-untyped-def]
        self.reset_count += 1
        self.events.append("reset")
        if self.fail_recovery and self.reset_count % 2 == 0:
            raise RuntimeError("scripted recovery failure")

    def verify_health(self, service, expected, client):  # type: ignore[no-untyped-def]
        self.events.append(f"health:{expected}")

    def activate(self, scenario, client):  # type: ignore[no-untyped-def]
        self.events.append("activate")

    def observe(self, expectation, client):  # type: ignore[no-untyped-def]
        self.events.append("observe")


def _runner(tmp_path: Path, *, stack=None, scenario_runner=None):  # type: ignore[no-untyped-def]
    scenario = ScenarioCatalog.load().get("users_unavailable")
    catalog = ScenarioCatalog((scenario,))
    storage = BenchmarkStorage(tmp_path)
    resolved_stack = stack or FakeStack()
    warmup = FakeWarmup()
    runner = BenchmarkRunner(
        AgentSettings(_env_file=None, enabled=True, model="scripted-model"),
        storage,
        resolved_stack,
        warmup,
        FakeEngine(),
        catalog,
        scenario_runner or FakeScenarioRunner(),  # type: ignore[arg-type]
    )
    return runner, storage, resolved_stack, warmup


def test_schema_versions_and_atomic_persistence_are_secret_free(tmp_path: Path) -> None:
    runner, storage, _, _ = _runner(tmp_path)
    manifest = runner.create_manifest(_identity(), BenchmarkMode.SMOKE, 1)
    directory = storage.create_session(manifest)
    loaded = storage.load_manifest(manifest.benchmark_session_id)
    assert loaded.benchmark_schema_version == 2
    assert loaded.evaluation_schema_version == 1
    assert not list(directory.rglob("*.tmp"))
    serialized = (directory / "manifest.json").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in serialized
    assert "Authorization" not in serialized


def test_runner_persists_each_run_and_resumes_without_repeating(tmp_path: Path) -> None:
    runner, storage, stack, warmup = _runner(tmp_path)
    manifest = runner.create_manifest(_identity(), BenchmarkMode.BASELINE, 2)
    storage.create_session(manifest)
    completed = asyncio.run(runner.run(manifest))
    assert completed.status is BenchmarkSessionStatus.COMPLETED
    assert len(storage.load_runs(manifest.benchmark_session_id)) == 2
    assert stack.calls == 2
    assert warmup.calls == 2

    resumed = asyncio.run(runner.run(completed))
    assert resumed.status is BenchmarkSessionStatus.COMPLETED
    assert stack.calls == 2


def test_partial_results_survive_later_stack_failure(tmp_path: Path) -> None:
    stack = FakeStack(fail_after=1)
    runner, storage, _, _ = _runner(tmp_path, stack=stack)
    manifest = runner.create_manifest(_identity(), BenchmarkMode.BASELINE, 2)
    storage.create_session(manifest)
    with pytest.raises(EnvironmentIntegrityError):
        asyncio.run(runner.run(manifest))
    records = storage.load_runs(manifest.benchmark_session_id)
    assert len(records) == 2
    assert records[0].recovery_verified is True
    assert records[1].run_status is BenchmarkRunStatus.SETUP_FAILURE
    persisted = storage.load_manifest(manifest.benchmark_session_id)
    assert persisted.status is BenchmarkSessionStatus.ENVIRONMENT_INTEGRITY_FAILURE


def test_recovery_failure_quarantines_session(tmp_path: Path) -> None:
    scenario_runner = FakeScenarioRunner(fail_recovery=True)
    runner, storage, _, _ = _runner(tmp_path, scenario_runner=scenario_runner)
    manifest = runner.create_manifest(_identity(), BenchmarkMode.SMOKE, 1)
    storage.create_session(manifest)
    with pytest.raises(EnvironmentIntegrityError):
        asyncio.run(runner.run(manifest))
    record = storage.load_runs(manifest.benchmark_session_id)[0]
    assert record.run_status is BenchmarkRunStatus.RESET_FAILURE
    assert record.recovery_verified is False
    assert storage.load_manifest(manifest.benchmark_session_id).status is (
        BenchmarkSessionStatus.ENVIRONMENT_INTEGRITY_FAILURE
    )


def test_resume_refuses_a_persisted_run_without_verified_recovery(tmp_path: Path) -> None:
    runner, storage, _, _ = _runner(tmp_path)
    manifest = runner.create_manifest(_identity(), BenchmarkMode.SMOKE, 1)
    storage.create_session(manifest)
    planned = manifest.actual_run_order[0]
    scenario = runner.catalog.get(planned.scenario_id)
    storage.write_run(
        runner._setup_failure(manifest, planned, scenario, RuntimeError("interrupted"))
    )

    with pytest.raises(EnvironmentIntegrityError, match="lacks verified recovery"):
        asyncio.run(runner.run(manifest))

    persisted = storage.load_manifest(manifest.benchmark_session_id)
    assert persisted.status is BenchmarkSessionStatus.ENVIRONMENT_INTEGRITY_FAILURE


def test_ordering_is_deterministic_and_seed_is_required(tmp_path: Path) -> None:
    runner, _, _, _ = _runner(tmp_path)
    with pytest.raises(ValueError, match="recorded seed"):
        runner.create_manifest(
            _identity(), BenchmarkMode.BASELINE, 2, OrderingStrategy.SEEDED_SHUFFLE
        )
    first = runner.create_manifest(
        _identity(), BenchmarkMode.BASELINE, 3, OrderingStrategy.SEEDED_SHUFFLE, 42
    )
    second = runner.create_manifest(
        _identity(), BenchmarkMode.BASELINE, 3, OrderingStrategy.SEEDED_SHUFFLE, 42
    )
    assert [(item.scenario_id, item.repetition) for item in first.actual_run_order] == [
        (item.scenario_id, item.repetition) for item in second.actual_run_order
    ]


def test_baseline_lock_requires_complete_reproducible_session(tmp_path: Path) -> None:
    runner, storage, _, _ = _runner(tmp_path)
    manifest = runner.create_manifest(_identity(), BenchmarkMode.BASELINE, 1)
    storage.create_session(manifest)
    with pytest.raises(BenchmarkStorageError, match="completed"):
        storage.lock_baseline(manifest.benchmark_session_id, "not-ready")
    completed = asyncio.run(runner.run(manifest))
    path = storage.lock_baseline(completed.benchmark_session_id, "accepted-v1")
    assert path.exists()
    assert "aggregate_result_sha256" in path.read_text(encoding="utf-8")


def test_baseline_lock_rejects_a_dirty_tree_session(tmp_path: Path) -> None:
    runner, storage, _, _ = _runner(tmp_path)
    manifest = runner.create_manifest(
        _identity(git_dirty=True, reproducible=False), BenchmarkMode.BASELINE, 1
    )
    storage.create_session(manifest)
    completed = asyncio.run(runner.run(manifest))

    with pytest.raises(BenchmarkStorageError, match="dirty-tree"):
        storage.lock_baseline(completed.benchmark_session_id, "dirty-v1")


def test_baseline_lock_rejects_smoke_session(tmp_path: Path) -> None:
    runner, storage, _, _ = _runner(tmp_path)
    manifest = runner.create_manifest(_identity(), BenchmarkMode.SMOKE, 1)
    storage.create_session(manifest)
    completed = asyncio.run(runner.run(manifest))

    with pytest.raises(BenchmarkStorageError, match="repeated baseline"):
        storage.lock_baseline(completed.benchmark_session_id, "smoke-v1")


def test_baseline_lock_rejects_a_mismatched_summary(tmp_path: Path) -> None:
    runner, storage, _, _ = _runner(tmp_path)
    manifest = runner.create_manifest(_identity(), BenchmarkMode.BASELINE, 1)
    storage.create_session(manifest)
    completed = asyncio.run(runner.run(manifest))
    summary = storage.load_summary(completed.benchmark_session_id)
    summary_path = storage.session_directory(completed.benchmark_session_id) / "summary.json"
    summary_path.write_text(
        summary.model_copy(update={"benchmark_session_id": uuid4()}).model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkStorageError, match="different session"):
        storage.lock_baseline(completed.benchmark_session_id, "mismatched-v1")


def _analysis_record(
    scenario: str,
    status: BenchmarkRunStatus,
    confidence: float,
    exact: bool,
) -> BenchmarkRunRecord:
    from nexus.evaluation.aegisops.models import ScenarioScore

    run_id = uuid4()
    score = ScenarioScore(
        scenario_id=scenario,
        repetition=1,
        component_correct=exact,
        failure_class_correct=exact,
        exact_diagnosis=exact,
        confidence=confidence,
        abstained=not exact,
        evidence_reference_count=2,
        valid_evidence_reference_count=2 if exact else 1,
        evidence_validity_rate=1 if exact else 0.5,
        unsupported_claim_count=0 if exact else 1,
        unsafe_request_count=0,
        tool_call_count=1,
        turn_count=1,
        duration_ms=10,
    )
    return BenchmarkRunRecord(
        benchmark_session_id=uuid4(),
        evaluation_run_id=run_id,
        run_index=1,
        scenario_id=scenario,
        failure_class="service_unavailable",
        repetition=1,
        run_status=status,
        timestamp=datetime.now(UTC),
        model="scripted-model",
        git_sha="a" * 40,
        instruction_hash="i" * 64,
        tool_registry_hash="t" * 64,
        diagnosis_schema_hash="d" * 64,
        agent_diagnosis=_diagnosis(),
        score=score,
        confidence=confidence,
        abstained=not exact,
        tool_calls=[
            ObservableToolCall(
                position=1,
                tool_call_id=uuid4(),
                tool="get_system_health",
                normalized_arguments={},
                timestamp=datetime.now(UTC),
                duration_ms=2,
                success=True,
                backend="service_health",
                result_count=4,
            )
        ],
        duration_ms=10,
        turn_count=1,
        accounting_complete=True,
        valid_evidence_references=score.valid_evidence_reference_count,
        invalid_evidence_references=score.evidence_reference_count
        - score.valid_evidence_reference_count,
        unsupported_claims=score.unsupported_claim_count,
        forbidden_capability_attempts=0,
        backend_failures=0,
        conflicting_evidence_references=0,
        recovery_verified=True,
    )


def test_analysis_reports_calibration_scenarios_tools_and_failures() -> None:
    records = [
        _analysis_record("users_unavailable", BenchmarkRunStatus.COMPLETED, 0.9, True),
        _analysis_record("users_unavailable", BenchmarkRunStatus.TIMEOUT, 0.3, False),
    ]
    summary = summarize(uuid4(), _identity(), records)
    assert summary.aggregate.exact_diagnosis_accuracy == 0.5
    assert summary.aggregate.timeout_rate == 0.5
    assert summary.aggregate.median_tool_calls == 1
    assert summary.aggregate.p95_tool_calls is None
    assert summary.scenarios[0].success_count == 1
    assert summary.tools[0].tool == "get_system_health"
    assert sum(bucket.count for bucket in summary.confidence_calibration) == 2
    assert all(bucket.small_sample for bucket in summary.confidence_calibration)
