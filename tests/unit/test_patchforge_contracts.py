"""PatchForge contract, attestation, hashing, and Atlas adapter tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from nexus.atlas.models import ArtifactReference, SourceRevision
from nexus.patchforge.canonical import canonical_json, canonical_sha256
from nexus.patchforge.models import (
    AgentReport,
    CheckKind,
    CheckResult,
    CheckStatus,
    DiffSummary,
    EngineeringTask,
    ExecutionStatus,
    PatchForgeFailure,
    PatchForgePhase,
    PatchOutcome,
    PatchResult,
    PhaseBudget,
    PhaseUsage,
    ReproductionEvidence,
    ReproductionStatus,
    RunBudgets,
    RunBudgetUsage,
    RunIdentity,
    ToolCallRecord,
    ToolCallStatus,
    ToolName,
)
from nexus.patchforge.models import (
    TestExecution as RuntimeTestExecution,
)

NOW = datetime(2026, 9, 28, 1, tzinfo=UTC)
SOURCE = SourceRevision(
    repository_url="https://github.com/gavinocvara/nexus-agent-platform",
    commit_sha="1" * 40,
)


def _phase_budget(*, calls: int = 2) -> PhaseBudget:
    return PhaseBudget(max_tool_calls=calls, max_duration_seconds=30, max_output_bytes=10_000)


def _budgets() -> RunBudgets:
    return RunBudgets(
        provisioning=_phase_budget(),
        recon=_phase_budget(),
        hypothesis=_phase_budget(),
        reproduce=_phase_budget(),
        implement=_phase_budget(),
        targeted_validate=_phase_budget(),
        full_validate=_phase_budget(),
        self_review=_phase_budget(),
        finalization_reserve=_phase_budget(calls=1),
        cleanup=_phase_budget(),
        max_implementation_loops=2,
        max_total_tool_calls=25,
        max_total_duration_seconds=400,
    )


def _identity() -> RunIdentity:
    return RunIdentity(
        run_id=UUID(int=1),
        task_id=UUID(int=2),
        atlas_job_id=UUID(int=3),
        atlas_execution_id=UUID(int=4),
        agent_id="patchforge.engineer",
        source=SOURCE,
        repository_profile_id="nexus.python",
        repository_profile_sha256="a" * 64,
        task_sha256="b" * 64,
        engine_kind="scripted",
        engine_version="v1",
        created_at=NOW,
    )


def _artifact(name: str, digest: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_type="execution_log" if name.endswith(".log") else "unified_diff",
        uri=f"atlas://artifacts/{name}",
        sha256=digest,
    )


def _tool_call(sequence: int, tool: ToolName, phase: PatchForgePhase) -> ToolCallRecord:
    return ToolCallRecord(
        call_id=UUID(int=10 + sequence),
        run_id=UUID(int=1),
        sequence=sequence,
        phase=phase,
        tool_name=tool,
        arguments_sha256=f"{sequence}" * 64,
        status=ToolCallStatus.SUCCEEDED,
        started_at=NOW + timedelta(seconds=sequence),
        completed_at=NOW + timedelta(seconds=sequence + 1),
        output_bytes=100,
        output_truncated=False,
    )


def _execution(
    execution_id: int,
    call_id: int,
    kind: CheckKind,
    phase: PatchForgePhase,
    status: ExecutionStatus,
) -> RuntimeTestExecution:
    return RuntimeTestExecution(
        execution_id=UUID(int=execution_id),
        run_id=UUID(int=1),
        tool_call_id=UUID(int=call_id),
        phase=phase,
        check_kind=kind,
        command_name=kind.value,
        command_sha256="c" * 64,
        status=status,
        exit_code=0 if status is ExecutionStatus.PASSED else 1,
        started_at=NOW + timedelta(seconds=10),
        completed_at=NOW + timedelta(seconds=11),
        output_artifact=_artifact(f"{kind.value}.log", "d" * 64),
        passed_count=1 if status is ExecutionStatus.PASSED else 0,
        failed_count=1 if status is ExecutionStatus.FAILED else 0,
        skipped_count=0,
    )


def _successful_result() -> PatchResult:
    targeted_call = _tool_call(1, ToolName.RUN_TARGETED_TESTS, PatchForgePhase.TARGETED_VALIDATE)
    full_call = _tool_call(2, ToolName.RUN_TEST_SUITE, PatchForgePhase.FULL_VALIDATE)
    targeted = _execution(
        21,
        targeted_call.call_id.int,
        CheckKind.TARGETED_TESTS,
        PatchForgePhase.TARGETED_VALIDATE,
        ExecutionStatus.PASSED,
    )
    full = _execution(
        22,
        full_call.call_id.int,
        CheckKind.FULL_TEST_SUITE,
        PatchForgePhase.FULL_VALIDATE,
        ExecutionStatus.PASSED,
    )
    return PatchResult(
        identity=_identity(),
        outcome=PatchOutcome.PATCH_PROPOSED,
        phase_reached=PatchForgePhase.FINALIZE,
        report=AgentReport(
            summary="Proposed a bounded fix.",
            hypothesis="The defect was caused by an incorrect boundary check.",
            implementation="Adjusted the boundary and added focused coverage.",
            evidence_references=[targeted.execution_id, full.execution_id],
        ),
        tool_calls=[targeted_call, full_call],
        executions=[targeted, full],
        reproduction=ReproductionEvidence(
            status=ReproductionStatus.NOT_PRACTICAL,
            rationale="The synthetic contract fixture has no runnable defect.",
        ),
        checks=[
            CheckResult(
                check_kind=CheckKind.TARGETED_TESTS,
                status=CheckStatus.PASSED,
                execution_id=targeted.execution_id,
                summary="Targeted tests passed.",
            ),
            CheckResult(
                check_kind=CheckKind.FULL_TEST_SUITE,
                status=CheckStatus.PASSED,
                execution_id=full.execution_id,
                summary="Full suite passed.",
            ),
        ],
        diff=DiffSummary(
            base_sha="1" * 40,
            proposed_head_sha="2" * 40,
            diff_sha256="e" * 64,
            patch_artifact=_artifact("change.diff", "e" * 64),
            changed_files=["src/nexus/example.py", "tests/test_example.py"],
            additions=8,
            deletions=2,
            test_files_changed=["tests/test_example.py"],
        ),
        budget_usage=RunBudgetUsage(
            phases=[
                PhaseUsage(
                    phase=PatchForgePhase.TARGETED_VALIDATE,
                    tool_calls=1,
                    duration_seconds=1.0,
                    output_bytes=100,
                ),
                PhaseUsage(
                    phase=PatchForgePhase.FULL_VALIDATE,
                    tool_calls=1,
                    duration_seconds=1.0,
                    output_bytes=100,
                ),
            ],
            total_tool_calls=2,
            total_duration_seconds=2.0,
            total_output_bytes=200,
            finalization_reserve_used=True,
        ),
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=20),
    )


def test_engineering_task_is_strict_and_repository_scoped() -> None:
    task = EngineeringTask(
        task_id=UUID(int=2),
        atlas_job_id=UUID(int=3),
        title="Correct boundary behavior",
        instructions="Make the smallest tested correction.",
        acceptance_criteria=["The boundary case passes."],
        source=SOURCE,
        repository_profile_id="nexus.python",
        repository_profile_sha256="a" * 64,
        scope_paths=["src/nexus", "tests"],
        created_at=NOW,
    )
    assert task.scope_paths == ["src/nexus", "tests"]

    invalid = task.model_dump(mode="python")
    invalid["atlas_job_id"] = str(task.atlas_job_id)
    with pytest.raises(ValidationError, match="is_instance_of"):
        EngineeringTask.model_validate(invalid)


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "C:\\secret", "src//file.py"])
def test_engineering_task_rejects_unsafe_scope_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="repository-relative"):
        EngineeringTask(
            task_id=UUID(int=2),
            atlas_job_id=UUID(int=3),
            title="Correct boundary behavior",
            instructions="Make the smallest tested correction.",
            acceptance_criteria=["The boundary case passes."],
            source=SOURCE,
            repository_profile_id="nexus.python",
            repository_profile_sha256="a" * 64,
            scope_paths=[path],
            created_at=NOW,
        )


def test_canonical_hash_is_order_independent_and_unicode_normalized() -> None:
    left = {"title": "cafe\u0301", "nested": {"b": 2, "a": 1}}
    right = {"nested": {"a": 1, "b": 2}, "title": "caf\u00e9"}
    assert canonical_json(left) == canonical_json(right)
    assert canonical_sha256(left) == canonical_sha256(right)


def test_run_budgets_keep_finalization_capacity_separate() -> None:
    budgets = _budgets()
    assert budgets.for_phase(PatchForgePhase.FINALIZE).max_tool_calls == 1
    with pytest.raises(ValueError, match="no execution budget"):
        budgets.for_phase(PatchForgePhase.CLOSED)

    invalid = budgets.model_dump(mode="python")
    invalid["max_total_tool_calls"] = 1
    with pytest.raises(ValidationError, match="exceed the run total"):
        RunBudgets.model_validate(invalid)


def test_agent_report_cannot_claim_runtime_validation_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AgentReport.model_validate(
            {
                "summary": "Looks good.",
                "hypothesis": "A boundary was wrong.",
                "implementation": "Changed it.",
                "tests_passed": True,
            }
        )


def test_execution_and_check_claims_must_match_runtime_evidence() -> None:
    result = _successful_result()
    invalid = result.model_dump(mode="python")
    invalid["checks"][0]["status"] = CheckStatus.FAILED
    with pytest.raises(ValidationError, match="does not match execution"):
        PatchResult.model_validate(invalid)


def test_agent_report_cannot_reference_fabricated_runtime_evidence() -> None:
    result = _successful_result()
    invalid = result.model_dump(mode="python")
    invalid["report"]["evidence_references"] = [UUID(int=999)]
    with pytest.raises(ValidationError, match="unknown runtime evidence"):
        PatchResult.model_validate(invalid)


def test_patch_proposed_requires_targeted_and_full_runtime_checks() -> None:
    result = _successful_result()
    invalid = result.model_dump(mode="python")
    invalid["checks"] = invalid["checks"][:1]
    with pytest.raises(ValidationError, match="targeted and full"):
        PatchResult.model_validate(invalid)


def test_patch_proposed_rejects_a_diff_from_another_source_sha() -> None:
    result = _successful_result()
    invalid = result.model_dump(mode="python")
    invalid["diff"]["base_sha"] = "9" * 40
    with pytest.raises(ValidationError, match="run source SHA"):
        PatchResult.model_validate(invalid)


def test_partial_result_is_complete_without_claiming_success() -> None:
    result = _successful_result()
    partial = result.model_copy(
        update={
            "outcome": PatchOutcome.PARTIAL,
            "phase_reached": PatchForgePhase.FINALIZE,
            "failure": PatchForgeFailure.BUDGET_EXHAUSTED,
            "diff": None,
            "checks": [],
            "executions": [],
            "tool_calls": [],
            "report": result.report.model_copy(update={"evidence_references": []}),
            "budget_usage": RunBudgetUsage(
                phases=[],
                total_tool_calls=0,
                total_duration_seconds=0.0,
                total_output_bytes=0,
                finalization_reserve_used=True,
            ),
        }
    )
    validated = PatchResult.model_validate(partial.model_dump(mode="python"))
    assert validated.outcome is PatchOutcome.PARTIAL
    with pytest.raises(ValueError, match="Only patch-proposed"):
        validated.to_atlas_result()


def test_runtime_attested_patch_converts_to_atlas_result() -> None:
    result = _successful_result()
    atlas_result = result.to_atlas_result()
    assert atlas_result.base_sha == SOURCE.commit_sha
    assert atlas_result.proposed_head_sha == "2" * 40
    assert atlas_result.changed_files == ["src/nexus/example.py", "tests/test_example.py"]
    assert all(check.passed for check in atlas_result.checks)
    replayed = PatchResult.model_validate_json(canonical_json(result))
    assert canonical_sha256(result) == canonical_sha256(replayed)
