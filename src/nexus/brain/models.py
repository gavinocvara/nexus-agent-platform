"""Strict schemas for private observable experience and memory."""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

BRAIN_SCHEMA_VERSION = 1
WRITER_POLICY_VERSION = "agent-observable-run-v1"
RETRIEVAL_ALGORITHM_VERSION = "bounded-lexical-v1"
RENDERER_VERSION = "untrusted-escaped-json-v1"
TOKENIZER_IDENTITY = "utf8-character-estimate-div4-v1"
AEGISOPS_AGENT_ID = "aegisops.investigator"
StrictConfig = ConfigDict(extra="forbid")
BoundedText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
Token = Annotated[
    str,
    StringConstraints(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$"),
]


class BrainMode(StrEnum):
    DISABLED = "disabled"
    LEARN = "learn"
    FROZEN_EVAL = "frozen_eval"


class MemoryType(StrEnum):
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"


class MemoryState(StrEnum):
    ACTIVE = "active"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"
    STALE = "stale"
    ARCHIVED = "archived"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"


class MemoryDerivation(StrEnum):
    SELF_REPORTED = "self_reported"
    OBSERVED_TRAJECTORY = "observed_trajectory"


class ProcedureStatus(StrEnum):
    CANDIDATE = "candidate"


class InvestigationCompletion(StrEnum):
    COMPLETED = "completed"
    TOOL_BUDGET_EXHAUSTED = "tool_budget_exhausted"
    TURN_LIMIT_EXHAUSTED = "turn_limit_exhausted"
    TIMED_OUT = "timed_out"
    INVALID_OUTPUT = "invalid_output"
    BACKEND_FAILURE = "backend_failure"
    FAILED = "failed"


class ObservableToolCall(BaseModel):
    """Allowlisted metadata for one tool call; no arguments or result payloads."""

    model_config = StrictConfig

    tool_call_id: UUID
    tool: Token
    success: bool
    backend: Token
    backend_error_code: Token | None = None
    backend_status_code: int | None = Field(default=None, ge=100, le=599)
    result_count: int = Field(ge=0)


class SelfReportedDiagnosis(BaseModel):
    """An agent claim, explicitly not semantic truth."""

    model_config = StrictConfig

    derivation: Literal[MemoryDerivation.SELF_REPORTED] = MemoryDerivation.SELF_REPORTED
    verification_status: Literal[VerificationStatus.UNVERIFIED] = VerificationStatus.UNVERIFIED
    status: Literal[
        "diagnosed",
        "insufficient_evidence",
        "diagnostic_backend_failure",
    ]
    component: Literal["gateway", "users", "orders", "postgres", "unknown"] | None
    failure_class: (
        Literal[
            "service_unavailable",
            "latency",
            "dependency_unavailable",
            "unknown",
        ]
        | None
    )
    confidence: float = Field(ge=0, le=1)
    evidence_tool_call_ids: list[UUID] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_claim(self) -> "SelfReportedDiagnosis":
        asserted = self.status == "diagnosed"
        if asserted and (self.component is None or self.failure_class is None):
            raise ValueError("A diagnosed self-report requires component and failure class")
        if not asserted and (self.component is not None or self.failure_class is not None):
            raise ValueError("An abstaining self-report cannot assert a diagnosis")
        return self


class AgentObservableRun(BaseModel):
    """The complete and exclusive input accepted by the Brain writer."""

    model_config = StrictConfig

    agent_id: Literal["aegisops.investigator"] = "aegisops.investigator"
    agent_run_id: UUID
    started_at: datetime
    finished_at: datetime
    runtime_outcome: InvestigationCompletion
    diagnosis: SelfReportedDiagnosis | None = None
    tool_calls: list[ObservableToolCall] = Field(default_factory=list, max_length=50)
    turn_count: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_times_and_evidence(self) -> "AgentObservableRun":
        if self.started_at.utcoffset() is None or self.finished_at.utcoffset() is None:
            raise ValueError("Observable run timestamps must be timezone-aware")
        if self.finished_at < self.started_at:
            raise ValueError("Observable run cannot finish before it starts")
        current_ids = {call.tool_call_id for call in self.tool_calls}
        if self.diagnosis is not None and not set(self.diagnosis.evidence_tool_call_ids).issubset(
            current_ids
        ):
            raise ValueError("Diagnosis evidence must reference current-run tool calls")
        return self


AGENT_OBSERVABLE_RUN_FIELDS = frozenset(AgentObservableRun.model_fields)


class MemoryProvenance(BaseModel):
    """Pointers resolve only to the private agent run and its tool calls."""

    model_config = StrictConfig

    source: Literal["agent_observable_run"] = "agent_observable_run"
    agent_run_id: UUID
    tool_call_ids: list[UUID] = Field(default_factory=list, max_length=50)


class EpisodicMemory(BaseModel):
    """Compact historical record of one private agent experience."""

    model_config = StrictConfig

    memory_type: Literal[MemoryType.EPISODIC] = MemoryType.EPISODIC
    diagnosis: SelfReportedDiagnosis | None = None
    evidence_patterns: list[BoundedText] = Field(default_factory=list, max_length=30)
    tools_used: list[Token] = Field(default_factory=list, max_length=20)
    tool_call_count: int = Field(ge=0, le=50)
    diagnostic_path: list[Token] = Field(default_factory=list, max_length=50)
    completion: InvestigationCompletion


class ProceduralMemory(BaseModel):
    """Unverified candidate procedure derived only from observable trajectories."""

    model_config = StrictConfig

    memory_type: Literal[MemoryType.PROCEDURAL] = MemoryType.PROCEDURAL
    derivation: Literal[MemoryDerivation.OBSERVED_TRAJECTORY] = MemoryDerivation.OBSERVED_TRAJECTORY
    verification_status: Literal[VerificationStatus.UNVERIFIED] = VerificationStatus.UNVERIFIED
    procedure_status: Literal[ProcedureStatus.CANDIDATE] = ProcedureStatus.CANDIDATE
    trigger_terms: list[Token] = Field(default_factory=list, max_length=30)
    candidate_next_steps: list[Token] = Field(default_factory=list, max_length=20)
    redundant_patterns: list[BoundedText] = Field(default_factory=list, max_length=20)
    budget_risk_sequences: list[BoundedText] = Field(default_factory=list, max_length=10)
    evidence_combinations: list[BoundedText] = Field(default_factory=list, max_length=20)
    source_episode_ids: list[UUID] = Field(min_length=1, max_length=20)


MemoryPayload = Annotated[EpisodicMemory | ProceduralMemory, Field(discriminator="memory_type")]


class MemoryRecord(BaseModel):
    """Canonical private memory envelope with lifecycle and provenance metadata."""

    model_config = StrictConfig

    id: UUID
    schema_version: Literal[1] = 1
    agent_id: Literal["aegisops.investigator"] = "aegisops.investigator"
    namespace: Literal["aegisops.investigator"] = "aegisops.investigator"
    memory_type: MemoryType
    created_at: datetime
    valid_from: datetime
    valid_to: datetime
    payload: MemoryPayload
    provenance: MemoryProvenance
    state: MemoryState = MemoryState.ACTIVE
    supersedes: list[UUID] = Field(default_factory=list, max_length=20)
    verification_status: Literal[VerificationStatus.UNVERIFIED] = VerificationStatus.UNVERIFIED
    retention_policy: Literal["private_brain_v1"] = "private_brain_v1"
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_record(self) -> "MemoryRecord":
        if self.payload.memory_type is not self.memory_type:
            raise ValueError("Memory envelope and payload types differ")
        serialized = str(self.model_dump(mode="json"))
        if contains_secret(serialized):
            raise ValueError("Memory content contains a forbidden secret-shaped value")
        lowered = serialized.lower()
        if any(marker in lowered for marker in _HARNESS_MARKERS):
            raise ValueError("Memory content contains a benchmark harness marker")
        timestamps = (self.created_at, self.valid_from, self.valid_to)
        if any(value.utcoffset() is None for value in timestamps):
            raise ValueError("Memory timestamps must be timezone-aware")
        if self.valid_to < self.valid_from:
            raise ValueError("Memory validity interval is inverted")
        return self

    @classmethod
    def now(cls) -> datetime:
        return datetime.now(UTC)


class BrainStorageStatus(StrEnum):
    DISABLED = "disabled"
    AVAILABLE = "available"
    READ_ONLY = "read_only"
    UNAVAILABLE = "unavailable"


class RetrievalStatus(StrEnum):
    DISABLED = "disabled"
    OK = "ok"
    EMPTY = "empty"
    FAILED = "failed"
    TIMEOUT = "timeout"


class RetrievedMemoryAudit(BaseModel):
    model_config = StrictConfig

    id: UUID
    memory_type: MemoryType
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class BrainRunMetadata(BaseModel):
    """Evaluator-side per-run Brain audit without memory content."""

    model_config = StrictConfig

    enabled: bool = False
    mode: BrainMode = BrainMode.DISABLED
    namespace: str | None = None
    schema_version: int | None = None
    writer_policy_version: str | None = None
    writer_allowlist_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    retrieval_algorithm_version: str | None = None
    renderer_template_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    tokenizer_identity: str | None = None
    storage_status: BrainStorageStatus = BrainStorageStatus.DISABLED
    retrieval_status: RetrievalStatus = RetrievalStatus.DISABLED
    retrieval_query_count: int = Field(default=0, ge=0, le=1)
    retrieval_latency_ms: float = Field(default=0, ge=0)
    retrieval_as_of: datetime | None = None
    max_retrieved_memories: int = Field(default=0, ge=0, le=8)
    max_context_tokens: int = Field(default=0, ge=0)
    max_context_chars: int = Field(default=0, ge=0)
    retrieved_memories: list[RetrievedMemoryAudit] = Field(default_factory=list, max_length=8)
    retrieved_memory_ids: list[UUID] = Field(default_factory=list, max_length=8)
    retrieved_memory_types: list[MemoryType] = Field(default_factory=list, max_length=8)
    procedural_memory_ids: list[UUID] = Field(default_factory=list, max_length=8)
    retrieval_count: int = Field(default=0, ge=0, le=8)
    rendered_context_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    rendered_context_chars: int = Field(default=0, ge=0)
    serialized_context_bytes: int = Field(default=0, ge=0)
    estimated_context_tokens: int = Field(default=0, ge=0)
    brain_attributable_input_tokens: int = Field(default=0, ge=0)
    context_truncated: bool = False
    experience_written: bool = False
    memory_write_count: int = Field(default=0, ge=0, le=2)
    write_latency_ms: float = Field(default=0, ge=0)
    written_memory_ids: list[UUID] = Field(default_factory=list, max_length=2)
    pre_snapshot_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    post_snapshot_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    snapshot_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    post_write_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    memory_type_counts: dict[MemoryType, int] = Field(default_factory=dict)


_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"authorization\s*:\s*bearer\s+\S+", re.IGNORECASE),
    re.compile(r"openai_api_key\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"postgres_password\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_HARNESS_MARKERS = ("scenario-runner", "scenario-probe", "benchmark-warmup")


def contains_secret(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _SECRET_PATTERNS)
