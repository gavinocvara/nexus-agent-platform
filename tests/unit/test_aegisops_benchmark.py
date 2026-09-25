import asyncio
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from nexus.aegisops.config import AgentSettings
from nexus.aegisops.models import (
    Component,
    Diagnosis,
    DiagnosisStatus,
    FailureClass,
    RootCauseHypothesis,
)
from nexus.aegisops.runtime import EngineOutcome
from nexus.brain.config import BrainSettings
from nexus.brain.manager import AegisOpsBrain
from nexus.brain.models import AEGISOPS_AGENT_ID, BrainRunMetadata, BrainStorageStatus
from nexus.brain.storage import SQLiteMemoryStore
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
    assert loaded.benchmark_schema_version == 3
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


def test_evaluator_scores_and_scenario_canaries_cannot_change_brain_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_time = datetime(2026, 9, 25, 1, tzinfo=UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return fixed_time

    class DiagnosedEngine:
        async def run(self, prompt, context, settings):  # type: ignore[no-untyped-def]
            return EngineOutcome(
                Diagnosis(
                    status=DiagnosisStatus.DIAGNOSED,
                    summary="Orders latency is the self-reported diagnosis.",
                    primary_hypothesis=RootCauseHypothesis(
                        component=Component.ORDERS,
                        failure_class=FailureClass.LATENCY,
                        rationale="Scripted agent-owned output.",
                        confidence=0.9,
                    ),
                    confidence=0.9,
                    next_diagnostic_action="Read current request evidence.",
                ),
                turn_count=1,
            )

    monkeypatch.setattr("nexus.aegisops.runtime.datetime", FixedDateTime)
    monkeypatch.setattr("nexus.aegisops.runtime.uuid4", lambda: UUID(int=700))
    base = ScenarioCatalog.load().get("orders_latency")
    scenarios = [
        base.model_copy(
            update={
                "id": "evaluator_canary_correct",
                "title": "EVALUATOR_CANARY_CORRECT_TITLE",
                "activation": base.activation.model_copy(
                    update={"failure_id": "evaluator_canary_correct"}
                ),
            }
        ),
        base.model_copy(
            update={
                "id": "evaluator_canary_wrong",
                "title": "EVALUATOR_CANARY_WRONG_TITLE",
                "activation": base.activation.model_copy(
                    update={"failure_id": "evaluator_canary_wrong"}
                ),
                "expected_root_cause": base.expected_root_cause.model_copy(
                    update={"component": "users", "failure": "service_unavailable"}
                ),
            }
        ),
    ]

    digests: list[str] = []
    exact_scores: list[bool] = []
    for index, scenario in enumerate(scenarios):
        root = tmp_path / str(index)
        storage = BenchmarkStorage(root / "benchmarks")
        brain_settings = BrainSettings(
            _env_file=None,
            mode="learn",
            path=root / "brain.sqlite3",
        )
        runner = BenchmarkRunner(
            AgentSettings(_env_file=None, enabled=True, model="scripted-model"),
            storage,
            FakeStack(),
            FakeWarmup(),
            DiagnosedEngine(),
            ScenarioCatalog((scenario,)),
            FakeScenarioRunner(),  # type: ignore[arg-type]
            brain_settings,
        )
        manifest = runner.create_manifest(_identity(), BenchmarkMode.TARGETED, 1)
        storage.create_session(manifest)
        asyncio.run(runner.run(manifest))
        record = storage.load_runs(manifest.benchmark_session_id)[0]
        assert record.score is not None
        exact_scores.append(record.score.exact_diagnosis)

        store = SQLiteMemoryStore(brain_settings.path)
        digests.append(store.logical_sha256(AEGISOPS_AGENT_ID))
        rendered = asyncio.run(
            AegisOpsBrain(brain_settings).retrieve(
                "Investigate the current production incident using read-only diagnostics.",
                as_of=fixed_time + timedelta(seconds=1),
            )
        ).prompt
        persisted = brain_settings.path.read_bytes()
        assert b"EVALUATOR_CANARY" not in persisted
        assert "EVALUATOR_CANARY" not in rendered
        assert scenario.id.encode() not in persisted
        assert scenario.id not in rendered

    assert exact_scores == [True, False]
    assert digests[0] == digests[1]


def test_baseline_lock_requires_complete_reproducible_session(tmp_path: Path) -> None:
    runner, storage, _, _ = _runner(tmp_path)
    manifest = runner.create_manifest(_identity(), BenchmarkMode.BASELINE, 1)
    storage.create_session(manifest)
    with pytest.raises(BenchmarkStorageError, match="completed"):
        storage.lock_baseline(manifest.benchmark_session_id, "not-ready")
    completed = asyncio.run(runner.run(manifest))
    path = storage.lock_baseline(completed.benchmark_session_id, "accepted-v1")
    assert path.exists()
    payload = path.read_text(encoding="utf-8")
    assert '"lock_schema_version": 2' in payload
    assert '"artifacts"' in payload
    assert "\\\\" not in payload
    assert storage.resolve_baseline("accepted-v1").name == "summary.json"


def test_portable_lock_artifact_count_must_match_run_count(tmp_path: Path) -> None:
    runner, storage, _, _ = _runner(tmp_path)
    manifest = runner.create_manifest(_identity(), BenchmarkMode.BASELINE, 2)
    storage.create_session(manifest)
    completed = asyncio.run(runner.run(manifest))
    path = storage.lock_baseline(completed.benchmark_session_id, "complete-v2")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["artifacts"]) == payload["run_count"] + 2

    payload["artifacts"].pop()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BenchmarkStorageError, match="Invalid baseline lock"):
        storage.resolve_baseline("complete-v2")


def test_legacy_lock_ignores_absolute_path_and_resolves_under_current_root(
    tmp_path: Path,
) -> None:
    storage = BenchmarkStorage(tmp_path)
    session_id = uuid4()
    session = storage.session_directory(session_id)
    session.mkdir(parents=True)
    summary = session / "summary.json"
    summary.write_text('{"legacy":true}\n', encoding="utf-8")
    lock = {
        "benchmark_schema_version": 3,
        "baseline_name": "legacy-v1",
        "benchmark_session_id": str(session_id),
        "git_sha": "a" * 40,
        "model": "gpt-5.6-sol",
        "accepted_at": "2026-09-24T19:46:21Z",
        "evaluation_schema_version": 1,
        "scenario_schema_version": 1,
        "run_count": 15,
        "aggregate_result_path": "C:\\old-host\\unusable\\summary.json",
        "aggregate_result_sha256": sha256(summary.read_bytes()).hexdigest(),
    }
    lock_path = tmp_path / "baselines" / "legacy-v1.json"
    lock_path.parent.mkdir()
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    assert storage.resolve_baseline("legacy-v1") == summary


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
    records[0] = records[0].model_copy(
        update={
            "brain": BrainRunMetadata(
                enabled=True,
                namespace="aegisops.investigator",
                storage_status=BrainStorageStatus.READ_ONLY,
                retrieved_memory_ids=[uuid4()],
                retrieved_memory_types=["procedural"],
                procedural_memory_ids=[uuid4()],
                retrieval_count=1,
                serialized_context_bytes=100,
                estimated_context_tokens=25,
            )
        }
    )
    summary = summarize(uuid4(), _identity(), records)
    assert summary.aggregate.exact_diagnosis_accuracy == 0.5
    assert summary.aggregate.timeout_rate == 0.5
    assert summary.aggregate.median_tool_calls == 1
    assert summary.aggregate.p95_tool_calls is None
    assert summary.scenarios[0].success_count == 1
    assert summary.tools[0].tool == "get_system_health"
    assert summary.aggregate.brain_retrieval_count == 1
    assert summary.aggregate.brain_memory_hit_rate == 0.5
    assert summary.aggregate.investigations_using_retrieved_memory == 1
    assert sum(bucket.count for bucket in summary.confidence_calibration) == 2
    assert all(bucket.small_sample for bucket in summary.confidence_calibration)
