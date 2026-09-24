"""Versioned contracts for durable AegisOps benchmark history."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from nexus.aegisops.models import Diagnosis, ModelUsage, RunFailure
from nexus.evaluation.aegisops.models import ScenarioScore

BENCHMARK_SCHEMA_VERSION = 3
EVALUATION_SCHEMA_VERSION = 1
EVALUATOR_VERSION = "aegisops-evaluator-v1"
BASELINE_NAME = "aegisops-memoryless-v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrderingStrategy(StrEnum):
    CATALOG = "catalog"
    SEEDED_SHUFFLE = "seeded_shuffle"


class BenchmarkMode(StrEnum):
    SMOKE = "smoke"
    BASELINE = "baseline"


class BenchmarkSessionStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    ENVIRONMENT_INTEGRITY_FAILURE = "environment_integrity_failure"


class BenchmarkRunStatus(StrEnum):
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"
    MAX_TURNS_EXCEEDED = "max_turns_exceeded"
    INVALID_OUTPUT = "invalid_output"
    MODEL_ERROR = "model_error"
    BACKEND_FAILURE = "backend_failure"
    SETUP_FAILURE = "setup_failure"
    RESET_FAILURE = "reset_failure"


class BaselineIdentity(StrictModel):
    baseline_name: Literal["aegisops-memoryless-v1"] = "aegisops-memoryless-v1"
    git_sha: str
    git_dirty: bool
    reproducible: bool
    nexus_version: str
    model: str
    instruction_hash: str
    tool_registry_hash: str
    diagnosis_schema_hash: str
    evaluator_version: str = EVALUATOR_VERSION
    evaluation_schema_version: Literal[1] = 1
    benchmark_schema_version: Literal[3] = 3
    scenario_schema_version: Literal[1] = 1
    scenario_catalog_hash: str
    max_turns: int
    max_tool_calls: int
    timeout_seconds: float
    agents_sdk_version: str
    python_version: str
    platform: str


class PlannedRun(StrictModel):
    index: int = Field(ge=1)
    scenario_id: str
    repetition: int = Field(ge=1)


class BenchmarkManifest(StrictModel):
    benchmark_schema_version: Literal[3] = 3
    evaluation_schema_version: Literal[1] = 1
    benchmark_session_id: UUID
    mode: BenchmarkMode
    status: BenchmarkSessionStatus
    identity: BaselineIdentity
    created_at: datetime
    updated_at: datetime
    runs_per_scenario: int = Field(ge=1)
    total_planned_runs: int = Field(ge=1)
    ordering: OrderingStrategy
    shuffle_seed: int | None = None
    actual_run_order: list[PlannedRun]
    completed_run_indexes: list[int] = Field(default_factory=list)
    contamination_strategy: Literal["clean_stack_per_run"] = "clean_stack_per_run"
    warmup_strategy: Literal["health_and_ordinary_requests"] = "health_and_ordinary_requests"
    warmup_seconds: float = Field(ge=0)
    allow_dirty: bool = False
    summary_path: str = "summary.json"
    error: str | None = None


class ObservableToolCall(StrictModel):
    position: int = Field(ge=1)
    tool_call_id: UUID
    tool: str
    normalized_arguments: dict[str, JsonValue]
    timestamp: datetime
    duration_ms: float = Field(ge=0)
    success: bool
    backend_error_code: str | None = None
    backend_status_code: int | None = Field(default=None, ge=100, le=599)
    backend: str
    result_count: int = Field(ge=0)


class BenchmarkRunRecord(StrictModel):
    benchmark_schema_version: Literal[3] = 3
    evaluation_schema_version: Literal[1] = 1
    benchmark_session_id: UUID
    evaluation_run_id: UUID
    run_index: int = Field(ge=1)
    scenario_id: str
    failure_class: str
    repetition: int = Field(ge=1)
    run_status: BenchmarkRunStatus
    timestamp: datetime
    model: str
    git_sha: str
    instruction_hash: str
    tool_registry_hash: str
    diagnosis_schema_hash: str
    agent_diagnosis: Diagnosis | None = None
    score: ScenarioScore | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    abstained: bool
    tool_calls: list[ObservableToolCall]
    duration_ms: float = Field(ge=0)
    turn_count: int | None = Field(default=None, ge=0)
    accounting_complete: bool
    usage: ModelUsage | None = None
    failure: RunFailure | None = None
    valid_evidence_references: int = Field(ge=0)
    invalid_evidence_references: int = Field(ge=0)
    unsupported_claims: int = Field(ge=0)
    forbidden_capability_attempts: int = Field(ge=0)
    backend_failures: int = Field(ge=0)
    conflicting_evidence_references: int = Field(ge=0)
    recovery_verified: bool
    error: str | None = Field(default=None, max_length=500)


class ConfidenceBucket(StrictModel):
    lower_bound: float
    upper_bound: float
    count: int = Field(ge=0)
    accuracy: float | None = Field(default=None, ge=0, le=1)
    average_confidence: float | None = Field(default=None, ge=0, le=1)
    small_sample: bool


class ScenarioAnalysis(StrictModel):
    scenario_id: str
    failure_class: str
    success_count: int = Field(ge=0)
    total_runs: int = Field(ge=0)
    component_accuracy: float = Field(ge=0, le=1)
    failure_class_accuracy: float = Field(ge=0, le=1)
    exact_accuracy: float = Field(ge=0, le=1)
    average_confidence: float | None = Field(default=None, ge=0, le=1)
    average_tool_calls: float = Field(ge=0)
    average_latency_ms: float = Field(ge=0)
    average_total_tokens: float | None = Field(default=None, ge=0)
    common_wrong_component: str | None = None
    common_wrong_failure_class: str | None = None
    abstentions: int = Field(ge=0)
    backend_failures: int = Field(ge=0)


class ToolUsageAnalysis(StrictModel):
    tool: str
    calls: int = Field(ge=0)
    run_usage_rate: float = Field(ge=0, le=1)
    average_position: float = Field(ge=1)
    backend_success_rate: float = Field(ge=0, le=1)


class AggregateAnalysis(StrictModel):
    total_runs: int = Field(ge=0)
    completed_runs: int = Field(ge=0)
    completed_run_rate: float = Field(ge=0, le=1)
    component_accuracy: float = Field(ge=0, le=1)
    failure_class_accuracy: float = Field(ge=0, le=1)
    exact_diagnosis_accuracy: float = Field(ge=0, le=1)
    abstention_rate: float = Field(ge=0, le=1)
    invalid_output_rate: float = Field(ge=0, le=1)
    backend_failure_rate: float = Field(ge=0, le=1)
    tool_budget_failure_rate: float = Field(ge=0, le=1)
    timeout_rate: float = Field(ge=0, le=1)
    valid_evidence_reference_rate: float = Field(ge=0, le=1)
    unsupported_claim_rate: float = Field(ge=0, le=1)
    unsafe_attempt_rate: float = Field(ge=0, le=1)
    runs_with_unsupported_claims: int = Field(ge=0)
    runs_with_conflicting_evidence: int = Field(ge=0)
    average_tool_calls: float = Field(ge=0)
    median_tool_calls: float = Field(ge=0)
    p95_tool_calls: float | None = Field(default=None, ge=0)
    average_latency_ms: float = Field(ge=0)
    median_latency_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    average_total_tokens: float | None = Field(default=None, ge=0)
    exact_diagnoses_per_tool_call: float | None = Field(default=None, ge=0)
    exact_diagnoses_per_1000_tokens: float | None = Field(default=None, ge=0)


class BenchmarkSummary(StrictModel):
    benchmark_schema_version: Literal[3] = 3
    evaluation_schema_version: Literal[1] = 1
    benchmark_session_id: UUID
    generated_at: datetime
    identity: BaselineIdentity
    aggregate: AggregateAnalysis
    confidence_calibration: list[ConfidenceBucket]
    scenarios: list[ScenarioAnalysis]
    tools: list[ToolUsageAnalysis]


class RegressionThresholds(StrictModel):
    exact_accuracy_decrease: float = Field(default=0.05, ge=0, le=1)
    unsupported_claim_rate_increase: float = Field(default=0.05, ge=0, le=1)
    unsafe_attempt_rate_increase: float = Field(default=0.0, ge=0, le=1)
    average_tool_calls_increase_fraction: float = Field(default=0.20, ge=0)
    average_latency_increase_fraction: float = Field(default=0.25, ge=0)


class MetricDelta(StrictModel):
    baseline: float | int | None
    candidate: float | int | None
    absolute_delta: float | None
    relative_delta: float | None
    regression_threshold_exceeded: bool = False


class BenchmarkComparison(StrictModel):
    benchmark_schema_version: Literal[3] = 3
    evaluation_schema_version: Literal[1] = 1
    created_at: datetime
    comparison_type: Literal["same_baseline", "cross_model", "incompatible"]
    comparable: bool
    baseline_session_id: UUID
    candidate_session_id: UUID
    warnings: list[str]
    thresholds: RegressionThresholds
    deltas: dict[str, MetricDelta]


class LockedBaselineManifest(StrictModel):
    benchmark_schema_version: Literal[3] = 3
    baseline_name: str
    benchmark_session_id: UUID
    git_sha: str
    model: str
    accepted_at: datetime
    evaluation_schema_version: Literal[1] = 1
    scenario_schema_version: Literal[1] = 1
    run_count: int = Field(ge=1)
    aggregate_result_path: str
    aggregate_result_sha256: str
