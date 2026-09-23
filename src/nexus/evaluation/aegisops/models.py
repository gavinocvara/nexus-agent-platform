"""Typed evaluator records and reproducibility metadata."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from nexus.aegisops.models import InvestigationRunRecord


class EvaluationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_at: datetime
    git_sha: str
    nexus_version: str
    model: str
    instruction_hash: str
    tool_registry_hash: str
    scenario_schema_version: int
    runs_per_scenario: int
    max_turns: int
    timeout_seconds: float
    max_tool_calls: int
    sdk_tracing_enabled: bool


class ScenarioScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    repetition: int = Field(ge=1)
    component_correct: bool
    failure_class_correct: bool
    exact_diagnosis: bool
    confidence: float | None = Field(default=None, ge=0, le=1)
    abstained: bool
    evidence_reference_count: int = Field(ge=0)
    valid_evidence_reference_count: int = Field(ge=0)
    evidence_validity_rate: float = Field(ge=0, le=1)
    unsupported_claim_count: int = Field(ge=0)
    unsafe_request_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    turn_count: int | None = Field(default=None, ge=0)
    duration_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class EvaluationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record: InvestigationRunRecord
    score: ScenarioScore


class AggregateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_count: int = Field(ge=0)
    component_accuracy: float = Field(ge=0, le=1)
    failure_class_accuracy: float = Field(ge=0, le=1)
    exact_diagnosis_accuracy: float = Field(ge=0, le=1)
    abstention_rate: float = Field(ge=0, le=1)
    evidence_validity_rate: float = Field(ge=0, le=1)
    mean_unsupported_claims: float = Field(ge=0)
    unsafe_request_count: int = Field(ge=0)
    mean_tool_calls: float = Field(ge=0)
    mean_turns: float | None = Field(default=None, ge=0)
    mean_latency_ms: float = Field(ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: EvaluationMetadata
    aggregate: AggregateResult
    runs: list[EvaluationRun]
