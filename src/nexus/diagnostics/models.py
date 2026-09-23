"""Typed contracts for the read-only AegisOps diagnostic boundary."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

StrictModelConfig = ConfigDict(extra="forbid")
CorrelationId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
]
TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{32}$")]


class DiagnosticService(StrEnum):
    """Operational components visible to an investigator."""

    GATEWAY = "gateway"
    USERS = "users"
    ORDERS = "orders"
    POSTGRES = "postgres"


class ApplicationService(StrEnum):
    """HTTP application services that emit logs and request metrics."""

    GATEWAY = "gateway"
    USERS = "users"
    ORDERS = "orders"


class DiagnosticWindow(StrEnum):
    """Bounded lookback windows accepted by diagnostic tools."""

    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"


class DiagnosticSource(StrEnum):
    """Permitted operational evidence sources."""

    TOPOLOGY = "topology"
    SERVICE_HEALTH = "service_health"
    PROMETHEUS = "prometheus"
    LOKI = "loki"
    TEMPO = "tempo"


class HealthState(StrEnum):
    """Normalized health states, including unreachable backends."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class LogLevel(StrEnum):
    """Structured log levels accepted as filters."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class DiagnosticResult(BaseModel):
    """Common result metadata used for reasoning and auditing."""

    model_config = StrictModelConfig

    tool: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: DiagnosticSource
    success: bool = True
    warnings: list[str] = Field(default_factory=list)


class NoArguments(BaseModel):
    """Explicit empty input schema for argument-free tools."""

    model_config = StrictModelConfig


class ServiceInput(BaseModel):
    """Input for one registered operational component."""

    model_config = StrictModelConfig
    service: DiagnosticService


class RequestSummaryInput(BaseModel):
    """Input for a bounded application request summary."""

    model_config = StrictModelConfig
    service: ApplicationService
    window: DiagnosticWindow = DiagnosticWindow.FIVE_MINUTES


class DependencySummaryInput(BaseModel):
    """Input for one registered HTTP dependency edge."""

    model_config = StrictModelConfig
    service: ApplicationService
    dependency: DiagnosticService
    window: DiagnosticWindow = DiagnosticWindow.FIVE_MINUTES

    @model_validator(mode="after")
    def validate_edge(self) -> "DependencySummaryInput":
        allowed = {
            (ApplicationService.GATEWAY, DiagnosticService.USERS),
            (ApplicationService.GATEWAY, DiagnosticService.ORDERS),
        }
        if (self.service, self.dependency) not in allowed:
            raise ValueError("Dependency is not a registered HTTP topology edge")
        return self


class DatabaseHealthInput(BaseModel):
    """Input for the only currently exposed database health metric."""

    model_config = StrictModelConfig
    service: Literal[ApplicationService.ORDERS] = ApplicationService.ORDERS


class LogSearchInput(BaseModel):
    """Approved structured filters for bounded log retrieval."""

    model_config = StrictModelConfig
    service: ApplicationService
    window: DiagnosticWindow = DiagnosticWindow.FIVE_MINUTES
    level: LogLevel | None = None
    correlation_id: CorrelationId | None = None
    status_code: int | None = Field(default=None, ge=100, le=599)
    limit: int = Field(default=20, ge=1, le=100)


class CorrelationInput(BaseModel):
    """Input for exact correlation lookup across application logs."""

    model_config = StrictModelConfig
    correlation_id: CorrelationId


class TraceInput(BaseModel):
    """Input for exact retrieval of one W3C trace identifier."""

    model_config = StrictModelConfig
    trace_id: TraceId


class RecentErrorsInput(BaseModel):
    """Input for a bounded recent-error lookup."""

    model_config = StrictModelConfig
    service: ApplicationService
    window: DiagnosticWindow = DiagnosticWindow.FIVE_MINUTES
    limit: int = Field(default=20, ge=1, le=100)


class ServiceDescriptor(BaseModel):
    """One investigator-visible topology component."""

    model_config = StrictModelConfig
    service: DiagnosticService
    kind: Literal["http_service", "database"]
    dependencies: list[DiagnosticService] = Field(default_factory=list)


class ServiceInventoryResult(DiagnosticResult):
    """Static operational service inventory."""

    services: list[ServiceDescriptor]


class ServiceHealthSnapshot(BaseModel):
    """Normalized health for one component and its visible dependencies."""

    model_config = StrictModelConfig
    service: DiagnosticService
    status: HealthState
    dependencies: dict[DiagnosticService, HealthState] = Field(default_factory=dict)


class ServiceHealthResult(DiagnosticResult):
    """Health result for one registered component."""

    health: ServiceHealthSnapshot | None = None


class SystemHealthResult(DiagnosticResult):
    """Bounded health snapshot for the complete visible topology."""

    services: list[ServiceHealthSnapshot]


class RequestSummary(BaseModel):
    """Normalized aggregate request evidence."""

    model_config = StrictModelConfig
    service: ApplicationService
    window: DiagnosticWindow
    request_rate_per_second: float | None = None
    error_rate_per_second: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None
    in_flight: float | None = None


class RequestSummaryResult(DiagnosticResult):
    """Prometheus-backed request summary result."""

    summary: RequestSummary | None = None


class DependencySummary(BaseModel):
    """Normalized aggregate evidence for one HTTP dependency edge."""

    model_config = StrictModelConfig
    service: ApplicationService
    dependency: DiagnosticService
    window: DiagnosticWindow
    request_rate_per_second: float | None = None
    failure_rate_per_second: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None


class DependencySummaryResult(DiagnosticResult):
    """Prometheus-backed dependency summary result."""

    summary: DependencySummary | None = None


class DatabaseHealthResult(DiagnosticResult):
    """Orders database health metric result."""

    service: Literal[ApplicationService.ORDERS] = ApplicationService.ORDERS
    dependency: Literal[DiagnosticService.POSTGRES] = DiagnosticService.POSTGRES
    healthy: bool | None = None


class LogEvent(BaseModel):
    """Allowlisted structured log fields; message content is untrusted data."""

    model_config = StrictModelConfig
    timestamp: datetime
    service: ApplicationService
    level: LogLevel | str
    message: str = Field(max_length=2000)
    correlation_id: str | None = Field(default=None, max_length=128)
    trace_id: str | None = None
    span_id: str | None = None
    method: str | None = Field(default=None, max_length=16)
    path: str | None = Field(default=None, max_length=500)
    status_code: int | None = None
    duration_ms: float | None = None


class LogSearchResult(DiagnosticResult):
    """Bounded chronological log evidence."""

    content_is_untrusted: Literal[True] = True
    events: list[LogEvent]


SafeAttribute = str | int | float | bool


class TraceSpan(BaseModel):
    """Normalized span with explicitly allowlisted attributes."""

    model_config = StrictModelConfig
    span_id: str
    parent_span_id: str | None = None
    service: str
    operation: str = Field(max_length=300)
    kind: str
    duration_ms: float
    status: str
    attributes: dict[str, SafeAttribute] = Field(default_factory=dict)


class TraceEvidence(BaseModel):
    """Normalized distributed trace evidence."""

    model_config = StrictModelConfig
    trace_id: str
    duration_ms: float
    services: list[str]
    spans: list[TraceSpan]


class TraceResult(DiagnosticResult):
    """Tempo-backed trace result containing only safe normalized fields."""

    content_is_untrusted: Literal[True] = True
    trace: TraceEvidence | None = None


class TraceSearchResult(DiagnosticResult):
    """Bounded trace identifiers found through exact log correlation."""

    trace_ids: list[str]


def json_safe_arguments(model: BaseModel) -> dict[str, Any]:
    """Return normalized arguments suitable for an audit event."""

    return model.model_dump(mode="json")
