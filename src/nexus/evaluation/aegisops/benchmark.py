"""Resume-safe live benchmark orchestration with per-run recovery guarantees."""

import random
from datetime import UTC, datetime
from uuid import uuid4

import httpx

from nexus.aegisops.config import AgentSettings
from nexus.aegisops.models import DiagnosisStatus, InvestigationRunRecord, RunStatus
from nexus.aegisops.runtime import GENERIC_INCIDENT_PROMPT, InvestigatorEngine, InvestigatorRuntime
from nexus.diagnostics.audit import DiagnosticSession
from nexus.diagnostics.service import DiagnosticServiceLayer
from nexus.evaluation.aegisops.analytics import summarize
from nexus.evaluation.aegisops.benchmark_models import (
    BaselineIdentity,
    BenchmarkManifest,
    BenchmarkMode,
    BenchmarkRunRecord,
    BenchmarkRunStatus,
    BenchmarkSessionStatus,
    ObservableToolCall,
    OrderingStrategy,
    PlannedRun,
)
from nexus.evaluation.aegisops.environment import (
    BenchmarkWarmup,
    EnvironmentIntegrityError,
    StackController,
)
from nexus.evaluation.aegisops.models import ScenarioScore
from nexus.evaluation.aegisops.scoring import score_scenario
from nexus.evaluation.aegisops.storage import BenchmarkStorage
from nexus.lab.catalog import ScenarioCatalog
from nexus.lab.runner import ScenarioRunner, default_service_urls
from nexus.lab.schema import FailureScenario, ScenarioService


class BenchmarkRunner:
    def __init__(
        self,
        settings: AgentSettings,
        storage: BenchmarkStorage,
        stack: StackController,
        warmup: BenchmarkWarmup,
        engine: InvestigatorEngine | None = None,
        catalog: ScenarioCatalog | None = None,
        scenario_runner: ScenarioRunner | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.stack = stack
        self.warmup = warmup
        self.engine = engine
        self.catalog = catalog or ScenarioCatalog.load()
        self.scenario_runner = scenario_runner or ScenarioRunner(
            self.catalog, default_service_urls()
        )

    def create_manifest(
        self,
        identity: BaselineIdentity,
        mode: BenchmarkMode,
        runs_per_scenario: int,
        ordering: OrderingStrategy = OrderingStrategy.CATALOG,
        shuffle_seed: int | None = None,
        warmup_seconds: float = 6,
        allow_dirty: bool = False,
    ) -> BenchmarkManifest:
        if runs_per_scenario < 1:
            raise ValueError("runs_per_scenario must be at least 1")
        if ordering is OrderingStrategy.SEEDED_SHUFFLE and shuffle_seed is None:
            raise ValueError("seeded_shuffle requires a recorded seed")
        if ordering is OrderingStrategy.CATALOG and shuffle_seed is not None:
            raise ValueError("catalog ordering cannot define a shuffle seed")
        planned_values = [
            (scenario.id, repetition)
            for scenario in self.catalog.list()
            for repetition in range(1, runs_per_scenario + 1)
        ]
        if ordering is OrderingStrategy.SEEDED_SHUFFLE:
            random.Random(shuffle_seed).shuffle(planned_values)
        plan = [
            PlannedRun(index=index, scenario_id=scenario_id, repetition=repetition)
            for index, (scenario_id, repetition) in enumerate(planned_values, 1)
        ]
        now = datetime.now(UTC)
        return BenchmarkManifest(
            benchmark_session_id=uuid4(),
            mode=mode,
            status=BenchmarkSessionStatus.CREATED,
            identity=identity,
            created_at=now,
            updated_at=now,
            runs_per_scenario=runs_per_scenario,
            total_planned_runs=len(plan),
            ordering=ordering,
            shuffle_seed=shuffle_seed,
            actual_run_order=plan,
            warmup_seconds=warmup_seconds,
            allow_dirty=allow_dirty,
        )

    async def run(self, manifest: BenchmarkManifest) -> BenchmarkManifest:
        existing = {
            record.run_index: record
            for record in self.storage.load_runs(manifest.benchmark_session_id)
        }
        incomplete = [record for record in existing.values() if not record.recovery_verified]
        if incomplete:
            manifest = self._update_manifest(
                manifest,
                BenchmarkSessionStatus.ENVIRONMENT_INTEGRITY_FAILURE,
                error="A persisted run lacks verified recovery; start a new benchmark session",
            )
            raise EnvironmentIntegrityError(manifest.error or "Recovery was not verified")
        completed = sorted(existing)
        manifest = self._update_manifest(
            manifest,
            BenchmarkSessionStatus.RUNNING,
            completed=completed,
        )
        for planned in manifest.actual_run_order:
            if planned.index in completed:
                continue
            scenario = self.catalog.get(planned.scenario_id)
            try:
                self.stack.clean_restart()
                self.warmup.run()
            except Exception as exc:
                failed = self._setup_failure(manifest, planned, scenario, exc)
                self.storage.write_run(failed)
                self._write_summary(manifest)
                manifest = self._update_manifest(
                    manifest,
                    BenchmarkSessionStatus.ENVIRONMENT_INTEGRITY_FAILURE,
                    error="Clean-stack preparation failed; session quarantined",
                )
                raise EnvironmentIntegrityError(
                    manifest.error or "Stack preparation failed"
                ) from exc
            await self._run_one(manifest, planned, scenario)
            completed.append(planned.index)
            manifest = self._update_manifest(
                manifest,
                BenchmarkSessionStatus.RUNNING,
                completed=sorted(completed),
            )
            self._write_summary(manifest)
        manifest = self._update_manifest(
            manifest,
            BenchmarkSessionStatus.COMPLETED,
            completed=sorted(completed),
        )
        self._write_summary(manifest)
        return manifest

    async def _run_one(
        self,
        manifest: BenchmarkManifest,
        planned: PlannedRun,
        scenario: FailureScenario,
    ) -> BenchmarkRunRecord:
        health_service = ScenarioService(scenario.recovery.health_service.value)
        benchmark_record: BenchmarkRunRecord | None = None
        with httpx.Client(timeout=15) as client:
            try:
                self.scenario_runner.reset(scenario, client)
                self.scenario_runner.verify_health(health_service, 200, client)
                self.scenario_runner.activate(scenario, client)
                for expectation in scenario.expected_symptoms:
                    self.scenario_runner.observe(expectation, client)
                session = DiagnosticSession(max_tool_calls=self.settings.max_tool_calls)
                with DiagnosticServiceLayer(session=session) as diagnostics:
                    runtime = InvestigatorRuntime(self.settings, self.engine)
                    agent_record = await runtime.investigate(
                        GENERIC_INCIDENT_PROMPT, diagnostics=diagnostics
                    )
                score = score_scenario(scenario, planned.repetition, agent_record, session)
                benchmark_record = self._record_from_agent(
                    manifest, planned, scenario, agent_record, session, score
                )
                self.storage.write_run(benchmark_record)
            except Exception as exc:
                if benchmark_record is None:
                    benchmark_record = self._setup_failure(manifest, planned, scenario, exc)
                    self.storage.write_run(benchmark_record)
            try:
                self.scenario_runner.reset(scenario, client)
                self.scenario_runner.verify_health(
                    health_service, scenario.recovery.expected_status, client
                )
            except Exception as exc:
                failed = benchmark_record.model_copy(
                    update={
                        "run_status": BenchmarkRunStatus.RESET_FAILURE,
                        "recovery_verified": False,
                        "error": f"Recovery verification failed ({type(exc).__name__})",
                    }
                )
                self.storage.write_run(failed)
                self._write_summary(manifest)
                self._update_manifest(
                    manifest,
                    BenchmarkSessionStatus.ENVIRONMENT_INTEGRITY_FAILURE,
                    error="Scenario recovery failed; session quarantined",
                )
                raise EnvironmentIntegrityError("Scenario recovery failed") from exc
        recovered = benchmark_record.model_copy(update={"recovery_verified": True})
        self.storage.write_run(recovered)
        return recovered

    def _record_from_agent(
        self,
        manifest: BenchmarkManifest,
        planned: PlannedRun,
        scenario: FailureScenario,
        record: InvestigationRunRecord,
        session: DiagnosticSession,
        score: ScenarioScore,
    ) -> BenchmarkRunRecord:
        events = session.audit_records()
        diagnosis = record.diagnosis
        status = _benchmark_status(record)
        return BenchmarkRunRecord(
            benchmark_session_id=manifest.benchmark_session_id,
            evaluation_run_id=record.run_id,
            run_index=planned.index,
            scenario_id=scenario.id,
            failure_class=scenario.fault.type.value,
            repetition=planned.repetition,
            run_status=status,
            timestamp=record.finished_at,
            model=record.model,
            git_sha=manifest.identity.git_sha,
            instruction_hash=manifest.identity.instruction_hash,
            tool_registry_hash=manifest.identity.tool_registry_hash,
            diagnosis_schema_hash=manifest.identity.diagnosis_schema_hash,
            agent_diagnosis=diagnosis,
            score=score,
            confidence=diagnosis.confidence if diagnosis is not None else None,
            abstained=score.abstained,
            tool_calls=[
                ObservableToolCall(
                    position=position,
                    tool_call_id=event.tool_call_id,
                    tool=event.tool,
                    normalized_arguments=event.normalized_arguments,
                    timestamp=event.timestamp,
                    duration_ms=event.duration_ms,
                    success=event.success,
                    backend_error_code=(
                        event.backend_error_code.value
                        if event.backend_error_code is not None
                        else None
                    ),
                    backend_status_code=event.backend_status_code,
                    backend=event.backend.value,
                    result_count=event.result_count,
                )
                for position, event in enumerate(events, 1)
            ],
            duration_ms=record.duration_ms,
            turn_count=record.turn_count,
            accounting_complete=record.accounting_complete,
            usage=record.usage,
            failure=record.failure,
            valid_evidence_references=score.valid_evidence_reference_count,
            invalid_evidence_references=(
                score.evidence_reference_count - score.valid_evidence_reference_count
            ),
            unsupported_claims=score.unsupported_claim_count,
            forbidden_capability_attempts=score.unsafe_request_count,
            backend_failures=sum(not event.success for event in events),
            conflicting_evidence_references=(
                len(diagnosis.conflicting_evidence) if diagnosis is not None else 0
            ),
            recovery_verified=False,
            error=record.error,
        )

    def _setup_failure(
        self,
        manifest: BenchmarkManifest,
        planned: PlannedRun,
        scenario: FailureScenario,
        error: Exception,
    ) -> BenchmarkRunRecord:
        return BenchmarkRunRecord(
            benchmark_session_id=manifest.benchmark_session_id,
            evaluation_run_id=uuid4(),
            run_index=planned.index,
            scenario_id=scenario.id,
            failure_class=scenario.fault.type.value,
            repetition=planned.repetition,
            run_status=BenchmarkRunStatus.SETUP_FAILURE,
            timestamp=datetime.now(UTC),
            model=manifest.identity.model,
            git_sha=manifest.identity.git_sha,
            instruction_hash=manifest.identity.instruction_hash,
            tool_registry_hash=manifest.identity.tool_registry_hash,
            diagnosis_schema_hash=manifest.identity.diagnosis_schema_hash,
            abstained=True,
            tool_calls=[],
            duration_ms=0,
            turn_count=0,
            accounting_complete=True,
            valid_evidence_references=0,
            invalid_evidence_references=0,
            unsupported_claims=0,
            forbidden_capability_attempts=0,
            backend_failures=0,
            conflicting_evidence_references=0,
            recovery_verified=False,
            error=f"Benchmark setup failed ({type(error).__name__})",
        )

    def _update_manifest(
        self,
        manifest: BenchmarkManifest,
        status: BenchmarkSessionStatus,
        *,
        completed: list[int] | None = None,
        error: str | None = None,
    ) -> BenchmarkManifest:
        updated = manifest.model_copy(
            update={
                "status": status,
                "updated_at": datetime.now(UTC),
                "completed_run_indexes": (
                    completed if completed is not None else manifest.completed_run_indexes
                ),
                "error": error,
            }
        )
        self.storage.write_manifest(updated)
        return updated

    def _write_summary(self, manifest: BenchmarkManifest) -> None:
        runs = self.storage.load_runs(manifest.benchmark_session_id)
        self.storage.write_summary(
            summarize(manifest.benchmark_session_id, manifest.identity, runs)
        )


def _benchmark_status(record: InvestigationRunRecord) -> BenchmarkRunStatus:
    mapping = {
        RunStatus.COMPLETED: BenchmarkRunStatus.COMPLETED,
        RunStatus.TIMED_OUT: BenchmarkRunStatus.TIMEOUT,
        RunStatus.TOOL_BUDGET_EXCEEDED: BenchmarkRunStatus.TOOL_BUDGET_EXCEEDED,
        RunStatus.MAX_TURNS_EXCEEDED: BenchmarkRunStatus.MAX_TURNS_EXCEEDED,
        RunStatus.INVALID_OUTPUT: BenchmarkRunStatus.INVALID_OUTPUT,
        RunStatus.MODEL_ERROR: BenchmarkRunStatus.MODEL_ERROR,
        RunStatus.DISABLED: BenchmarkRunStatus.MODEL_ERROR,
        RunStatus.MISSING_CREDENTIALS: BenchmarkRunStatus.MODEL_ERROR,
    }
    if (
        record.diagnosis is not None
        and record.diagnosis.status is DiagnosisStatus.DIAGNOSTIC_BACKEND_FAILURE
    ):
        return BenchmarkRunStatus.BACKEND_FAILURE
    return mapping[record.status]
