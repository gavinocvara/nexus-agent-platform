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

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


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
    turn_count: int = Field(ge=0)
    diagnostic_session_id: UUID
    diagnosis: Diagnosis | None = None
    usage: ModelUsage | None = None
    error: str | None = Field(default=None, max_length=500)

    @classmethod
    def now(cls) -> datetime:
        return datetime.now(UTC)
