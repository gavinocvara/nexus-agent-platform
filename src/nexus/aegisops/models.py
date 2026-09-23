"""Structured diagnosis and run-record contracts for one investigator."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

StrictConfig = ConfigDict(extra="forbid")
EvidencePath = Annotated[
    str,
    StringConstraints(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.]+$"),
]


class DiagnosisStatus(StrEnum):
    DIAGNOSED = "diagnosed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    DIAGNOSTIC_BACKEND_FAILURE = "diagnostic_backend_failure"


class Component(StrEnum):
    GATEWAY = "gateway"
    USERS = "users"
    ORDERS = "orders"
    POSTGRES = "postgres"
    UNKNOWN = "unknown"


class FailureClass(StrEnum):
    SERVICE_UNAVAILABLE = "service_unavailable"
    LATENCY = "latency"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    UNKNOWN = "unknown"


class RunStatus(StrEnum):
    COMPLETED = "completed"
    DISABLED = "disabled"
    MISSING_CREDENTIALS = "missing_credentials"
    TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"
    MAX_TURNS_EXCEEDED = "max_turns_exceeded"
    TIMED_OUT = "timed_out"
    INVALID_OUTPUT = "invalid_output"
    MODEL_ERROR = "model_error"


class FailureCategory(StrEnum):
    CREDENTIALS = "credentials"
    TOOL_BUDGET = "tool_budget"
    TURN_LIMIT = "turn_limit"
    TIMEOUT = "timeout"
    OUTPUT_VALIDATION = "output_validation"
    MODEL_BEHAVIOR = "model_behavior"
    TOOL_EXECUTION = "tool_execution"
    PROVIDER = "provider"
    SDK = "sdk"
    INTERNAL = "internal"


class FailureOrigin(StrEnum):
    CONFIGURATION = "configuration"
    BUDGET_ENFORCEMENT = "budget_enforcement"
    FINAL_OUTPUT_VALIDATION = "final_output_validation"
    TOOL_EXECUTION = "tool_execution"
    PROVIDER_TRANSPORT = "provider_transport"
    SDK_RUNTIME = "sdk_runtime"
    INTERNAL_RUNTIME = "internal_runtime"


class RootCauseHypothesis(BaseModel):
    model_config = StrictConfig

    component: Component
    failure_class: FailureClass
    rationale: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1)


class EvidenceReference(BaseModel):
    """A claim linked to an exact value in one recorded diagnostic result."""

    model_config = StrictConfig

    tool_call_id: UUID
    tool: str = Field(min_length=1, max_length=100)
    result_path: EvidencePath
    observed_value: JsonValue
    observation: str = Field(min_length=1, max_length=1000)


class Diagnosis(BaseModel):
    model_config = StrictConfig

    status: DiagnosisStatus
    summary: str = Field(min_length=1, max_length=2000)
    primary_hypothesis: RootCauseHypothesis | None = None
    supporting_evidence: list[EvidenceReference] = Field(default_factory=list, max_length=20)
    conflicting_evidence: list[EvidenceReference] = Field(default_factory=list, max_length=10)
    alternatives: list[RootCauseHypothesis] = Field(default_factory=list, max_length=3)
    confidence: float = Field(ge=0, le=1)
    next_diagnostic_action: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_status(self) -> "Diagnosis":
        if self.status is DiagnosisStatus.DIAGNOSED and self.primary_hypothesis is None:
            raise ValueError("A diagnosed result requires a primary hypothesis")
        if self.status is not DiagnosisStatus.DIAGNOSED and self.primary_hypothesis is not None:
            raise ValueError("An abstaining result cannot assert a primary hypothesis")
        return self


class ModelUsage(BaseModel):
    model_config = StrictConfig

    request_count: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_write_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_output_tokens: int | None = Field(default=None, ge=0)


class RunFailure(BaseModel):
    """Sanitized failure metadata that never stores exception messages or payloads."""

    model_config = StrictConfig

    category: FailureCategory
    origin: FailureOrigin
    outer_exception_type: str = Field(min_length=1, max_length=200)
    cause_chain_types: list[str] = Field(default_factory=list, max_length=12)
    provider_status_code: int | None = Field(default=None, ge=100, le=599)
    provider_error_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")


class InvestigationRunRecord(BaseModel):
    """Auditable metadata without prompts, transcripts, or duplicated evidence."""

    model_config = StrictConfig

    run_id: UUID
    agent: str = "aegisops.investigator"
    model: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    turn_count: int | None = Field(default=None, ge=0)
    accounting_complete: bool
    diagnostic_session_id: UUID
    diagnosis: Diagnosis | None = None
    usage: ModelUsage | None = None
    failure: RunFailure | None = None
    error: str | None = Field(default=None, max_length=500)

    @classmethod
    def now(cls) -> datetime:
        return datetime.now(UTC)
