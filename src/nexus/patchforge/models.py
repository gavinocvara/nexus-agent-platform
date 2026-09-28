"""Strict PatchForge task, run, evidence, report, and result contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, Field, StringConstraints, model_validator

from nexus.atlas.models import (
    ArtifactReference,
    CommitSha,
    Identifier,
    Sha256,
    SourceRevision,
    StrictModel,
)
from nexus.atlas.models import CheckResult as AtlasCheckResult
from nexus.atlas.models import PatchResult as AtlasPatchResult

PATCHFORGE_SCHEMA_VERSION = 1
PATCHFORGE_CONTRACT_VERSION: Literal["patchforge-contract-v1"] = "patchforge-contract-v1"
RuntimeIssuer = Literal["patchforge.runtime"]
Narrative = Annotated[str, StringConstraints(min_length=1, max_length=4000)]
ShortNarrative = Annotated[str, StringConstraints(min_length=1, max_length=500)]


def _repository_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if (
        normalized in {"", "."}
        or normalized.startswith(("/", "~"))
        or ":" in parts[0]
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("Path must be a normalized repository-relative path")
    return normalized


RepositoryPath = Annotated[
    str,
    StringConstraints(min_length=1, max_length=500),
    AfterValidator(_repository_path),
]


class PatchForgePhase(StrEnum):
    CREATED = "created"
    PROVISIONING = "provisioning"
    RECON = "recon"
    HYPOTHESIS = "hypothesis"
    REPRODUCE = "reproduce"
    IMPLEMENT = "implement"
    TARGETED_VALIDATE = "targeted_validate"
    FULL_VALIDATE = "full_validate"
    SELF_REVIEW = "self_review"
    FINALIZE = "finalize"
    REPORTED = "reported"
    CLEANUP = "cleanup"
    CLOSED = "closed"


class PatchOutcome(StrEnum):
    PATCH_PROPOSED = "patch_proposed"
    PARTIAL = "partial"
    ABORTED = "aborted"
    CANCELLED = "cancelled"
    SANDBOX_FAILED = "sandbox_failed"
    POLICY_VIOLATION = "policy_violation"


class ToolName(StrEnum):
    LIST_TREE = "list_tree"
    SEARCH_CODE = "search_code"
    READ_FILE_RANGE = "read_file_range"
    INSPECT_SYMBOL = "inspect_symbol"
    GIT_STATUS = "git_status"
    GIT_DIFF = "git_diff"
    INSPECT_DIFF = "inspect_diff"
    WRITE_PATCH = "write_patch"
    CREATE_FILE = "create_file"
    DELETE_FILE = "delete_file"
    RUN_TARGETED_TESTS = "run_targeted_tests"
    RUN_TEST_SUITE = "run_test_suite"
    RUN_FORMATTER = "run_formatter"
    RUN_LINTER = "run_linter"
    RUN_TYPECHECK = "run_typecheck"
    INSPECT_TEST_FAILURE = "inspect_test_failure"
    ADVANCE_PHASE = "advance_phase"
    SUBMIT_REPORT = "submit_report"


class ToolCallStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


class CheckKind(StrEnum):
    REPRODUCTION = "reproduction"
    TARGETED_TESTS = "targeted_tests"
    FULL_TEST_SUITE = "full_test_suite"
    FORMATTER = "formatter"
    LINTER = "linter"
    TYPECHECK = "typecheck"


class ExecutionStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    TIMED_OUT = "timed_out"


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    NOT_RUN = "not_run"


class ReproductionStatus(StrEnum):
    FAIL_BEFORE_PASS_AFTER = "fail_before_pass_after"
    FAIL_BEFORE_NO_PASS = "fail_before_no_pass"
    PASS_BEFORE = "pass_before"
    NOT_PRACTICAL = "not_practical"


class PatchForgeFailure(StrEnum):
    BUDGET_EXHAUSTED = "budget_exhausted"
    POLICY_DENIED = "policy_denied"
    CANCELLED = "cancelled"
    SANDBOX_ERROR = "sandbox_error"
    WORKSPACE_ERROR = "workspace_error"
    ENGINE_ERROR = "engine_error"
    VALIDATION_FAILED = "validation_failed"
    ATTESTATION_FAILED = "attestation_failed"
    CLEANUP_FAILED = "cleanup_failed"


class FindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class PhaseBudget(StrictModel):
    max_tool_calls: int = Field(ge=0, le=10_000)
    max_duration_seconds: int = Field(ge=1, le=86_400)
    max_output_bytes: int = Field(ge=0, le=100_000_000)


class RunBudgets(StrictModel):
    """Visible phase envelopes; finalize capacity is structurally separate."""

    provisioning: PhaseBudget
    recon: PhaseBudget
    hypothesis: PhaseBudget
    reproduce: PhaseBudget
    implement: PhaseBudget
    targeted_validate: PhaseBudget
    full_validate: PhaseBudget
    self_review: PhaseBudget
    finalization_reserve: PhaseBudget
    cleanup: PhaseBudget
    max_implementation_loops: int = Field(ge=0, le=20)
    max_total_tool_calls: int = Field(ge=1, le=100_000)
    max_total_duration_seconds: int = Field(ge=1, le=604_800)

    @model_validator(mode="after")
    def validate_totals_and_reserve(self) -> "RunBudgets":
        phases = (
            self.provisioning,
            self.recon,
            self.hypothesis,
            self.reproduce,
            self.implement,
            self.targeted_validate,
            self.full_validate,
            self.self_review,
            self.finalization_reserve,
            self.cleanup,
        )
        if sum(item.max_tool_calls for item in phases) > self.max_total_tool_calls:
            raise ValueError("Phase tool budgets exceed the run total")
        if sum(item.max_duration_seconds for item in phases) > self.max_total_duration_seconds:
            raise ValueError("Phase duration budgets exceed the run total")
        if self.finalization_reserve.max_tool_calls < 1:
            raise ValueError("Finalization must reserve at least one tool call")
        if self.finalization_reserve.max_output_bytes < 1:
            raise ValueError("Finalization must reserve output capacity")
        return self

    def for_phase(self, phase: PatchForgePhase) -> PhaseBudget:
        field_name = {
            PatchForgePhase.PROVISIONING: "provisioning",
            PatchForgePhase.RECON: "recon",
            PatchForgePhase.HYPOTHESIS: "hypothesis",
            PatchForgePhase.REPRODUCE: "reproduce",
            PatchForgePhase.IMPLEMENT: "implement",
            PatchForgePhase.TARGETED_VALIDATE: "targeted_validate",
            PatchForgePhase.FULL_VALIDATE: "full_validate",
            PatchForgePhase.SELF_REVIEW: "self_review",
            PatchForgePhase.FINALIZE: "finalization_reserve",
            PatchForgePhase.CLEANUP: "cleanup",
        }.get(phase)
        if field_name is None:
            raise ValueError(f"Phase has no execution budget: {phase}")
        return cast(PhaseBudget, getattr(self, field_name))


class EngineeringTask(StrictModel):
    schema_version: Literal[1] = 1
    task_id: UUID
    atlas_job_id: UUID
    title: ShortNarrative
    instructions: Narrative
    acceptance_criteria: list[ShortNarrative] = Field(min_length=1, max_length=50)
    constraints: list[ShortNarrative] = Field(default_factory=list, max_length=50)
    source: SourceRevision
    repository_profile_id: Identifier
    repository_profile_sha256: Sha256
    scope_paths: list[RepositoryPath] = Field(default_factory=list, max_length=100)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def require_unique_scope(self) -> "EngineeringTask":
        if len(set(self.scope_paths)) != len(self.scope_paths):
            raise ValueError("Task scope paths must be unique")
        return self


class RunIdentity(StrictModel):
    schema_version: Literal[1] = 1
    run_id: UUID
    task_id: UUID
    atlas_job_id: UUID
    atlas_execution_id: UUID
    agent_id: Identifier
    source: SourceRevision
    repository_profile_id: Identifier
    repository_profile_sha256: Sha256
    task_sha256: Sha256
    contract_version: Literal["patchforge-contract-v1"] = PATCHFORGE_CONTRACT_VERSION
    engine_kind: Identifier
    engine_version: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    parallel_tool_calls: Literal[False] = False
    memory_mode: Literal["disabled"] = "disabled"
    created_at: AwareDatetime


class ToolCallRecord(StrictModel):
    schema_version: Literal[1] = 1
    call_id: UUID
    run_id: UUID
    sequence: int = Field(ge=1)
    phase: PatchForgePhase
    tool_name: ToolName
    arguments_sha256: Sha256
    status: ToolCallStatus
    started_at: AwareDatetime
    completed_at: AwareDatetime
    output_bytes: int = Field(ge=0)
    output_truncated: bool
    error_code: Identifier | None = None
    attested_by: RuntimeIssuer = "patchforge.runtime"

    @model_validator(mode="after")
    def validate_timing_and_status(self) -> "ToolCallRecord":
        if self.completed_at < self.started_at:
            raise ValueError("Tool call completion cannot precede start")
        if self.status is ToolCallStatus.SUCCEEDED and self.error_code is not None:
            raise ValueError("Successful tool calls cannot carry an error code")
        if self.status is not ToolCallStatus.SUCCEEDED and self.error_code is None:
            raise ValueError("Unsuccessful tool calls require an error code")
        return self


class TestExecution(StrictModel):
    schema_version: Literal[1] = 1
    execution_id: UUID
    run_id: UUID
    tool_call_id: UUID
    phase: PatchForgePhase
    check_kind: CheckKind
    command_name: Identifier
    command_sha256: Sha256
    status: ExecutionStatus
    exit_code: int | None = Field(default=None, ge=0, le=255)
    started_at: AwareDatetime
    completed_at: AwareDatetime
    output_artifact: ArtifactReference | None = None
    passed_count: int | None = Field(default=None, ge=0)
    failed_count: int | None = Field(default=None, ge=0)
    skipped_count: int | None = Field(default=None, ge=0)
    attested_by: RuntimeIssuer = "patchforge.runtime"

    @model_validator(mode="after")
    def validate_execution(self) -> "TestExecution":
        if self.completed_at < self.started_at:
            raise ValueError("Execution completion cannot precede start")
        if self.status is ExecutionStatus.PASSED and self.exit_code != 0:
            raise ValueError("Passed execution requires exit code zero")
        if self.status is ExecutionStatus.FAILED and (
            self.exit_code is None or self.exit_code == 0
        ):
            raise ValueError("Failed execution requires a nonzero exit code")
        if self.status is ExecutionStatus.TIMED_OUT and self.exit_code is not None:
            raise ValueError("Timed-out execution cannot claim an exit code")
        return self


class ReproductionEvidence(StrictModel):
    schema_version: Literal[1] = 1
    status: ReproductionStatus
    before_execution: TestExecution | None = None
    after_execution: TestExecution | None = None
    rationale: ShortNarrative
    attested_by: RuntimeIssuer = "patchforge.runtime"

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> "ReproductionEvidence":
        before = self.before_execution
        after = self.after_execution
        if self.status is ReproductionStatus.FAIL_BEFORE_PASS_AFTER:
            if before is None or before.status is not ExecutionStatus.FAILED:
                raise ValueError("Fail-before/pass-after requires a failed before execution")
            if after is None or after.status is not ExecutionStatus.PASSED:
                raise ValueError("Fail-before/pass-after requires a passed after execution")
        elif self.status is ReproductionStatus.FAIL_BEFORE_NO_PASS:
            if before is None or before.status is not ExecutionStatus.FAILED:
                raise ValueError("Failed reproduction requires a failed before execution")
            if after is not None and after.status is ExecutionStatus.PASSED:
                raise ValueError("Failed reproduction cannot contain a passed after execution")
        elif self.status is ReproductionStatus.PASS_BEFORE:
            if before is None or before.status is not ExecutionStatus.PASSED or after is not None:
                raise ValueError("Pass-before requires only a passed before execution")
        elif before is not None or after is not None:
            raise ValueError("Not-practical reproduction cannot claim test executions")
        return self


class CheckResult(StrictModel):
    schema_version: Literal[1] = 1
    check_kind: CheckKind
    status: CheckStatus
    execution_id: UUID | None = None
    summary: ShortNarrative
    attested_by: RuntimeIssuer = "patchforge.runtime"

    @model_validator(mode="after")
    def validate_evidence_reference(self) -> "CheckResult":
        if (self.status is CheckStatus.NOT_RUN) != (self.execution_id is None):
            raise ValueError("Only not-run checks may omit execution evidence")
        return self


class DiffSummary(StrictModel):
    schema_version: Literal[1] = 1
    base_sha: CommitSha
    proposed_head_sha: CommitSha
    diff_sha256: Sha256
    patch_artifact: ArtifactReference
    changed_files: list[RepositoryPath] = Field(min_length=1, max_length=1000)
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    binary_files: list[RepositoryPath] = Field(default_factory=list, max_length=100)
    protected_path_violations: list[RepositoryPath] = Field(default_factory=list, max_length=100)
    scope_violations: list[RepositoryPath] = Field(default_factory=list, max_length=100)
    test_files_changed: list[RepositoryPath] = Field(default_factory=list, max_length=500)
    attested_by: RuntimeIssuer = "patchforge.runtime"

    @model_validator(mode="after")
    def validate_diff(self) -> "DiffSummary":
        collections = (
            self.changed_files,
            self.binary_files,
            self.protected_path_violations,
            self.scope_violations,
            self.test_files_changed,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("Diff path collections must be unique")
        if self.diff_sha256 != self.patch_artifact.sha256:
            raise ValueError("Diff hash must match the patch artifact hash")
        return self


class PolicyFinding(StrictModel):
    finding_id: UUID
    severity: FindingSeverity
    code: Identifier
    detail: ShortNarrative
    path: RepositoryPath | None = None
    attested_by: RuntimeIssuer = "patchforge.runtime"


class AgentReport(StrictModel):
    """Model-authored narrative only; validation claims live in runtime evidence."""

    summary: Narrative
    hypothesis: Narrative
    implementation: Narrative
    limitations: list[ShortNarrative] = Field(default_factory=list, max_length=50)
    evidence_references: list[UUID] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def require_unique_evidence_references(self) -> "AgentReport":
        if len(set(self.evidence_references)) != len(self.evidence_references):
            raise ValueError("Agent evidence references must be unique")
        return self


class PhaseUsage(StrictModel):
    phase: PatchForgePhase
    tool_calls: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    output_bytes: int = Field(ge=0)


class RunBudgetUsage(StrictModel):
    phases: list[PhaseUsage] = Field(default_factory=list, max_length=20)
    total_tool_calls: int = Field(ge=0)
    total_duration_seconds: float = Field(ge=0)
    total_output_bytes: int = Field(ge=0)
    finalization_reserve_used: bool
    attested_by: RuntimeIssuer = "patchforge.runtime"

    @model_validator(mode="after")
    def validate_usage(self) -> "RunBudgetUsage":
        if len({item.phase for item in self.phases}) != len(self.phases):
            raise ValueError("Phase usage entries must be unique")
        if sum(item.tool_calls for item in self.phases) != self.total_tool_calls:
            raise ValueError("Phase tool usage does not match the total")
        if sum(item.output_bytes for item in self.phases) != self.total_output_bytes:
            raise ValueError("Phase output usage does not match the total")
        return self


class PatchResult(StrictModel):
    schema_version: Literal[1] = 1
    identity: RunIdentity
    outcome: PatchOutcome
    phase_reached: PatchForgePhase
    report: AgentReport
    tool_calls: list[ToolCallRecord] = Field(default_factory=list, max_length=10_000)
    executions: list[TestExecution] = Field(default_factory=list, max_length=1000)
    reproduction: ReproductionEvidence | None = None
    checks: list[CheckResult] = Field(default_factory=list, max_length=100)
    diff: DiffSummary | None = None
    policy_findings: list[PolicyFinding] = Field(default_factory=list, max_length=1000)
    budget_usage: RunBudgetUsage
    failure: PatchForgeFailure | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime
    attested_by: RuntimeIssuer = "patchforge.runtime"

    @model_validator(mode="after")
    def validate_result(self) -> "PatchResult":
        if self.completed_at < self.started_at:
            raise ValueError("Patch result completion cannot precede start")
        if any(item.run_id != self.identity.run_id for item in self.tool_calls):
            raise ValueError("Tool-call evidence belongs to a different run")
        if any(item.run_id != self.identity.run_id for item in self.executions):
            raise ValueError("Execution evidence belongs to a different run")
        sequences = [item.sequence for item in self.tool_calls]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("Tool-call sequence must be contiguous and ordered")
        execution_ids = {item.execution_id for item in self.executions}
        tool_call_ids = {item.call_id for item in self.tool_calls}
        if len(tool_call_ids) != len(self.tool_calls):
            raise ValueError("Tool-call IDs must be unique")
        if len(execution_ids) != len(self.executions):
            raise ValueError("Execution IDs must be unique")
        if len({item.check_kind for item in self.checks}) != len(self.checks):
            raise ValueError("Check kinds must be unique")
        finding_ids = {item.finding_id for item in self.policy_findings}
        if len(finding_ids) != len(self.policy_findings):
            raise ValueError("Policy-finding IDs must be unique")
        known_evidence_ids = tool_call_ids | execution_ids | finding_ids
        if not set(self.report.evidence_references).issubset(known_evidence_ids):
            raise ValueError("Agent report references unknown runtime evidence")
        if any(item.tool_call_id not in tool_call_ids for item in self.executions):
            raise ValueError("Execution references unknown tool-call evidence")
        if any(
            check.execution_id is not None and check.execution_id not in execution_ids
            for check in self.checks
        ):
            raise ValueError("Check references unknown execution evidence")
        executions_by_id = {item.execution_id: item for item in self.executions}
        expected_status = {
            CheckStatus.PASSED: {ExecutionStatus.PASSED},
            CheckStatus.FAILED: {ExecutionStatus.FAILED},
            CheckStatus.ERROR: {ExecutionStatus.ERROR, ExecutionStatus.TIMED_OUT},
        }
        for check in self.checks:
            if check.execution_id is None:
                continue
            execution = executions_by_id[check.execution_id]
            if execution.check_kind is not check.check_kind:
                raise ValueError("Check kind does not match execution evidence")
            if execution.status not in expected_status[check.status]:
                raise ValueError("Check status does not match execution evidence")
        if self.budget_usage.total_tool_calls != len(self.tool_calls):
            raise ValueError("Budget usage does not match recorded tool calls")
        if self.reproduction is not None:
            reproduction_ids = {
                item.execution_id
                for item in (
                    self.reproduction.before_execution,
                    self.reproduction.after_execution,
                )
                if item is not None
            }
            if not reproduction_ids.issubset(execution_ids):
                raise ValueError("Reproduction references unknown execution evidence")
        if self.outcome is PatchOutcome.PATCH_PROPOSED:
            if self.diff is None or self.failure is not None:
                raise ValueError("Patch-proposed outcome requires a diff and no failure")
            if self.diff.base_sha != self.identity.source.commit_sha:
                raise ValueError("Patch diff base does not match the run source SHA")
            if self.phase_reached not in {
                PatchForgePhase.FINALIZE,
                PatchForgePhase.REPORTED,
                PatchForgePhase.CLEANUP,
                PatchForgePhase.CLOSED,
            }:
                raise ValueError("Patch-proposed outcome must reach finalization")
            if any(item.severity is FindingSeverity.BLOCKING for item in self.policy_findings):
                raise ValueError("Patch-proposed outcome cannot contain blocking findings")
            if self.reproduction is None:
                raise ValueError("Patch-proposed outcome requires reproduction evidence")
            checks = {item.check_kind: item for item in self.checks}
            required = {CheckKind.TARGETED_TESTS, CheckKind.FULL_TEST_SUITE}
            if not required.issubset(checks):
                raise ValueError("Patch-proposed outcome requires targeted and full test checks")
            if any(item.status is not CheckStatus.PASSED for item in self.checks):
                raise ValueError("Patch-proposed outcome cannot contain an unpassed check")
        elif self.failure is None:
            raise ValueError("Non-success PatchForge outcomes require a failure classification")
        return self

    def to_atlas_result(self) -> AtlasPatchResult:
        """Convert only a runtime-attested patch proposal to Atlas' outer contract."""

        if self.outcome is not PatchOutcome.PATCH_PROPOSED or self.diff is None:
            raise ValueError("Only patch-proposed results can be submitted to Atlas")
        return AtlasPatchResult(
            summary=self.report.summary,
            base_sha=self.diff.base_sha,
            proposed_head_sha=self.diff.proposed_head_sha,
            patch_artifact=self.diff.patch_artifact,
            changed_files=self.diff.changed_files,
            checks=[
                AtlasCheckResult(
                    name=item.check_kind.value,
                    passed=item.status is CheckStatus.PASSED,
                    summary=item.summary,
                )
                for item in self.checks
            ],
        )


def elapsed_seconds(started_at: datetime, completed_at: datetime) -> float:
    """Return a nonnegative deterministic duration for runtime accounting."""

    return max(0.0, (completed_at - started_at).total_seconds())
