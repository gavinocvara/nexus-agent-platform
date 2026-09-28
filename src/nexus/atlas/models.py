"""Strict contracts for Atlas jobs, policy, results, approvals, and audit."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

ATLAS_SCHEMA_VERSION = 1
StrictConfig = ConfigDict(extra="forbid", frozen=True, strict=True)
Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$",
    ),
]
CommandId = Annotated[
    str,
    StringConstraints(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9_.:-]+$"),
]
BoundedText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
CommitSha = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")]


class StrictModel(BaseModel):
    model_config = StrictConfig


class JobStatus(StrEnum):
    CREATED = "created"
    VALIDATED = "validated"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalState(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ActorType(StrEnum):
    SYSTEM = "system"
    HUMAN = "human"
    AGENT = "agent"


class Capability(StrEnum):
    """Closed Phase 8 capability vocabulary; none grants unrestricted shell access."""

    SOURCE_READ = "repository.source.read"
    WORKTREE_WRITE = "repository.worktree.write"
    TEST_EXECUTE = "test.execute"
    PATCH_CREATE = "patch.create"
    REVIEW_READ = "review.read"
    REVIEW_SUBMIT = "review.submit"


class FailureClassification(StrEnum):
    VALIDATION_ERROR = "validation_error"
    PERMISSION_DENIED = "permission_denied"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT = "timeout"
    RUNTIME_ERROR = "runtime_error"
    REVIEW_FAILED = "review_failed"
    CANCELLED = "cancelled"
    PERSISTENCE_ERROR = "persistence_error"
    UNKNOWN = "unknown"


class ReviewVerdict(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class AuditEventType(StrEnum):
    JOB_CREATED = "job_created"
    JOB_VALIDATED = "job_validated"
    JOB_QUEUED = "job_queued"
    JOB_STARTED = "job_started"
    RESULT_RECORDED = "result_recorded"
    REVIEW_RECORDED = "review_recorded"
    JOB_APPROVED = "job_approved"
    JOB_REJECTED = "job_rejected"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    JOB_RECOVERED = "job_recovered"


class ActorIdentity(StrictModel):
    actor_type: ActorType
    actor_id: Identifier


class SourceRevision(StrictModel):
    repository_url: HttpUrl
    commit_sha: CommitSha
    ref: Annotated[str, StringConstraints(min_length=1, max_length=255)] | None = None

    @model_validator(mode="after")
    def reject_url_credentials_and_suffixes(self) -> "SourceRevision":
        if any(
            value is not None
            for value in (
                self.repository_url.username,
                self.repository_url.password,
                self.repository_url.query,
                self.repository_url.fragment,
            )
        ):
            raise ValueError("Repository URL cannot contain credentials, query, or fragment")
        return self


class ArtifactReference(StrictModel):
    artifact_type: Identifier
    uri: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    sha256: Sha256


class TaskRequest(StrictModel):
    task_type: Identifier
    title: ShortText
    instructions: BoundedText
    acceptance_criteria: list[ShortText] = Field(default_factory=list, max_length=50)
    inputs: list[ArtifactReference] = Field(default_factory=list, max_length=50)


class ExecutionBudget(StrictModel):
    max_duration_seconds: int = Field(ge=1, le=86_400)
    max_tool_calls: int = Field(ge=0, le=10_000)
    max_input_tokens: int = Field(ge=0, le=10_000_000)
    max_output_tokens: int = Field(ge=0, le=10_000_000)
    max_artifact_bytes: int = Field(ge=0, le=10_000_000_000)


class CheckResult(StrictModel):
    name: Identifier
    passed: bool
    summary: ShortText


class GenericJobResult(StrictModel):
    result_type: Literal["generic"] = "generic"
    summary: BoundedText
    artifacts: list[ArtifactReference] = Field(default_factory=list, max_length=100)
    checks: list[CheckResult] = Field(default_factory=list, max_length=100)


class PatchResult(StrictModel):
    """Minimal future PatchForge-to-Atlas result contract; no PatchForge behavior."""

    result_type: Literal["patch"] = "patch"
    summary: BoundedText
    base_sha: CommitSha
    proposed_head_sha: CommitSha
    patch_artifact: ArtifactReference
    changed_files: list[Annotated[str, StringConstraints(min_length=1, max_length=500)]] = Field(
        min_length=1,
        max_length=500,
    )
    checks: list[CheckResult] = Field(default_factory=list, max_length=100)

    @field_validator("changed_files")
    @classmethod
    def validate_relative_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            normalized = value.replace("\\", "/")
            if normalized.startswith("/") or any(part == ".." for part in normalized.split("/")):
                raise ValueError("Changed files must be repository-relative paths")
        if len(set(values)) != len(values):
            raise ValueError("Changed files must be unique")
        return values


StructuredResult = Annotated[GenericJobResult | PatchResult, Field(discriminator="result_type")]


class ReviewResult(StrictModel):
    review_id: UUID
    reviewer_agent_id: Identifier
    verdict: ReviewVerdict
    summary: BoundedText
    evidence: list[ArtifactReference] = Field(default_factory=list, max_length=100)
    created_at: AwareDatetime


class ApprovalRecord(StrictModel):
    approval_id: UUID
    decision: ApprovalDecision
    decided_by: ActorIdentity
    reason: ShortText
    decided_at: AwareDatetime

    @model_validator(mode="after")
    def require_human_actor(self) -> "ApprovalRecord":
        if self.decided_by.actor_type is not ActorType.HUMAN:
            raise ValueError("Atlas approvals require a human actor")
        return self


class JobFailure(StrictModel):
    classification: FailureClassification
    message: ShortText
    retryable: bool
    failed_by: ActorIdentity


class ExecutionLease(StrictModel):
    execution_id: UUID
    claimed_by: Identifier
    started_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_window(self) -> "ExecutionLease":
        if self.expires_at <= self.started_at:
            raise ValueError("Execution lease must expire after it starts")
        return self


class JobSpec(StrictModel):
    agent_id: Identifier
    reviewer_agent_id: Identifier
    request: TaskRequest
    source: SourceRevision
    budget: ExecutionBudget
    capabilities: list[Capability] = Field(min_length=1, max_length=20)

    @field_validator("capabilities")
    @classmethod
    def canonicalize_capabilities(cls, values: list[Capability]) -> list[Capability]:
        if len(set(values)) != len(values):
            raise ValueError("Capabilities must be unique")
        return sorted(values, key=lambda value: value.value)


class Job(StrictModel):
    schema_version: Literal[1] = 1
    job_id: UUID
    create_idempotency_key: CommandId
    agent_id: Identifier
    reviewer_agent_id: Identifier
    request: TaskRequest
    source: SourceRevision
    budget: ExecutionBudget
    capabilities: list[Capability] = Field(min_length=1, max_length=20)
    status: JobStatus = JobStatus.CREATED
    approval_state: ApprovalState = ApprovalState.NOT_REQUESTED
    result: StructuredResult | None = None
    review: ReviewResult | None = None
    approval: ApprovalRecord | None = None
    failure: JobFailure | None = None
    active_execution: ExecutionLease | None = None
    last_execution_id: UUID | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None = None
    review_requested_at: AwareDatetime | None = None
    terminal_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, values: list[Capability]) -> list[Capability]:
        if len(set(values)) != len(values):
            raise ValueError("Capabilities must be unique")
        if values != sorted(values, key=lambda value: value.value):
            raise ValueError("Capabilities must use canonical ordering")
        return values

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "Job":
        if self.updated_at < self.created_at:
            raise ValueError("Job update time cannot precede creation")
        if (self.status is JobStatus.RUNNING) != (self.active_execution is not None):
            raise ValueError("Only running jobs may hold an active execution lease")
        result_states = {
            JobStatus.AWAITING_REVIEW,
            JobStatus.APPROVED,
            JobStatus.REJECTED,
            JobStatus.COMPLETED,
        }
        if self.status in result_states and self.result is None:
            raise ValueError("Result presence does not match job status")
        if self.status not in result_states | {JobStatus.FAILED} and self.result is not None:
            raise ValueError("Result presence does not match job status")
        if (self.result is not None) != (self.review_requested_at is not None):
            raise ValueError("Result and review-request time must be recorded together")
        approval_states = {
            JobStatus.APPROVED: ApprovalState.APPROVED,
            JobStatus.REJECTED: ApprovalState.REJECTED,
            JobStatus.COMPLETED: ApprovalState.APPROVED,
        }
        if self.status in approval_states:
            if self.review is None or self.approval is None:
                raise ValueError("Reviewed terminal path requires review and approval records")
            if self.approval_state is not approval_states[self.status]:
                raise ValueError("Approval state does not match job status")
        elif self.status is JobStatus.AWAITING_REVIEW:
            if self.approval is not None or self.approval_state is not ApprovalState.PENDING:
                raise ValueError("Awaiting-review jobs require pending approval")
        elif self.status is not JobStatus.FAILED:
            if self.review is not None or self.approval is not None:
                raise ValueError("Review data is not valid before review is requested")
            if self.approval_state is not ApprovalState.NOT_REQUESTED:
                raise ValueError("Approval cannot change before review")
        if (self.status is JobStatus.FAILED) != (self.failure is not None):
            raise ValueError("Failure details must exist only for failed jobs")
        terminal = self.status in {JobStatus.COMPLETED, JobStatus.FAILED}
        if terminal != (self.terminal_at is not None):
            raise ValueError("Terminal timestamp does not match job status")
        if self.status is JobStatus.COMPLETED and (
            self.approval is None or self.approval.decision is not ApprovalDecision.APPROVED
        ):
            raise ValueError("Completed jobs require approval")
        if self.status is JobStatus.REJECTED and (
            self.approval is None or self.approval.decision is not ApprovalDecision.REJECTED
        ):
            raise ValueError("Rejected jobs require rejection")
        return self


class AgentPolicy(StrictModel):
    agent_id: Identifier
    runtime_kind: Identifier
    runtime_version: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    capabilities: list[Capability] = Field(min_length=1, max_length=20)
    max_budget: ExecutionBudget
    repository_prefixes: list[HttpUrl] = Field(min_length=1, max_length=20)

    @field_validator("capabilities")
    @classmethod
    def canonicalize_capabilities(cls, values: list[Capability]) -> list[Capability]:
        if len(set(values)) != len(values):
            raise ValueError("Capabilities must be unique")
        return sorted(values, key=lambda value: value.value)

    @field_validator("repository_prefixes")
    @classmethod
    def validate_repository_prefixes(cls, values: list[HttpUrl]) -> list[HttpUrl]:
        for value in values:
            if any(
                item is not None
                for item in (value.username, value.password, value.query, value.fragment)
            ):
                raise ValueError("Repository prefixes cannot contain credentials or suffixes")
        return values


class CreatedAuditData(StrictModel):
    kind: Literal["created"] = "created"
    request_sha256: Sha256
    create_idempotency_key: CommandId


class TransitionAuditData(StrictModel):
    kind: Literal["transition"] = "transition"
    reason: ShortText


class ResultAuditData(StrictModel):
    kind: Literal["result"] = "result"
    result_sha256: Sha256
    execution_id: UUID


class ReviewAuditData(StrictModel):
    kind: Literal["review"] = "review"
    review_id: UUID
    verdict: ReviewVerdict


class ApprovalAuditData(StrictModel):
    kind: Literal["approval"] = "approval"
    approval_id: UUID
    decision: ApprovalDecision


class FailureAuditData(StrictModel):
    kind: Literal["failure"] = "failure"
    classification: FailureClassification


class RecoveryAuditData(StrictModel):
    kind: Literal["recovery"] = "recovery"
    expired_execution_id: UUID


AuditData = Annotated[
    CreatedAuditData
    | TransitionAuditData
    | ResultAuditData
    | ReviewAuditData
    | ApprovalAuditData
    | FailureAuditData
    | RecoveryAuditData,
    Field(discriminator="kind"),
]


class AuditEvent(StrictModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    job_id: UUID
    sequence: int = Field(ge=1)
    job_revision: int = Field(ge=0)
    event_type: AuditEventType
    actor: ActorIdentity
    command_id: CommandId
    occurred_at: AwareDatetime
    from_status: JobStatus | None
    to_status: JobStatus
    data: AuditData

    @model_validator(mode="after")
    def validate_event_shape(self) -> "AuditEvent":
        expected_kind = {
            AuditEventType.JOB_CREATED: "created",
            AuditEventType.JOB_VALIDATED: "transition",
            AuditEventType.JOB_QUEUED: "transition",
            AuditEventType.JOB_STARTED: "transition",
            AuditEventType.RESULT_RECORDED: "result",
            AuditEventType.REVIEW_RECORDED: "review",
            AuditEventType.JOB_APPROVED: "approval",
            AuditEventType.JOB_REJECTED: "approval",
            AuditEventType.JOB_COMPLETED: "transition",
            AuditEventType.JOB_FAILED: "failure",
            AuditEventType.JOB_RECOVERED: "recovery",
        }[self.event_type]
        if self.data.kind != expected_kind:
            raise ValueError("Audit payload does not match event type")
        if self.event_type is AuditEventType.JOB_CREATED:
            if self.from_status is not None or self.to_status is not JobStatus.CREATED:
                raise ValueError("Job-created event has invalid status boundary")
        elif self.event_type is AuditEventType.REVIEW_RECORDED:
            if not (
                self.from_status is JobStatus.AWAITING_REVIEW
                and self.to_status is JobStatus.AWAITING_REVIEW
            ):
                raise ValueError("Review event cannot change job status")
        elif self.from_status is None or self.from_status is self.to_status:
            raise ValueError("Lifecycle events require an explicit state change")
        if self.sequence != self.job_revision + 1:
            raise ValueError("Audit sequence must track the resulting job revision")
        return self


class CommandRecord(StrictModel):
    command_id: CommandId
    operation: Identifier
    request_sha256: Sha256
    job_id: UUID
    response_revision: int = Field(ge=0)
    created_at: AwareDatetime
