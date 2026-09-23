"""Deterministic, framework-neutral registry of diagnostic tools."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from nexus.diagnostics.models import (
    CorrelationInput,
    DatabaseHealthInput,
    DependencySummaryInput,
    DiagnosticSource,
    LogSearchInput,
    NoArguments,
    RecentErrorsInput,
    RequestSummaryInput,
    ServiceInput,
    TraceInput,
)


class ToolDefinition(BaseModel):
    """Stable tool metadata suitable for a future Atlas registry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    input_schema: dict[str, JsonValue]
    read_only: Literal[True] = True
    source: DiagnosticSource
    risk: Literal["low"] = "low"


def _definition(
    name: str,
    description: str,
    input_model: type[NoArguments]
    | type[ServiceInput]
    | type[RequestSummaryInput]
    | type[DependencySummaryInput]
    | type[DatabaseHealthInput]
    | type[LogSearchInput]
    | type[CorrelationInput]
    | type[TraceInput]
    | type[RecentErrorsInput],
    source: DiagnosticSource,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema=input_model.model_json_schema(),
        source=source,
    )


TOOL_REGISTRY: tuple[ToolDefinition, ...] = (
    _definition(
        "list_services",
        "List visible operational topology.",
        NoArguments,
        DiagnosticSource.TOPOLOGY,
    ),
    _definition(
        "get_service_health",
        "Read one registered component's health.",
        ServiceInput,
        DiagnosticSource.SERVICE_HEALTH,
    ),
    _definition(
        "get_system_health",
        "Read a bounded system health snapshot.",
        NoArguments,
        DiagnosticSource.SERVICE_HEALTH,
    ),
    _definition(
        "get_request_summary",
        "Summarize bounded request metrics.",
        RequestSummaryInput,
        DiagnosticSource.PROMETHEUS,
    ),
    _definition(
        "get_dependency_summary",
        "Summarize one registered dependency edge.",
        DependencySummaryInput,
        DiagnosticSource.PROMETHEUS,
    ),
    _definition(
        "get_database_health",
        "Read the Orders PostgreSQL health metric.",
        DatabaseHealthInput,
        DiagnosticSource.PROMETHEUS,
    ),
    _definition(
        "search_logs",
        "Search logs with approved structured filters.",
        LogSearchInput,
        DiagnosticSource.LOKI,
    ),
    _definition(
        "get_request_evidence",
        "Find chronological logs for one correlation ID.",
        CorrelationInput,
        DiagnosticSource.LOKI,
    ),
    _definition(
        "get_trace", "Retrieve one exact normalized trace.", TraceInput, DiagnosticSource.TEMPO
    ),
    _definition(
        "find_traces",
        "Find trace IDs through exact correlation logs.",
        CorrelationInput,
        DiagnosticSource.LOKI,
    ),
    _definition(
        "get_recent_errors",
        "Retrieve bounded recent error events.",
        RecentErrorsInput,
        DiagnosticSource.LOKI,
    ),
)


def get_tool_registry() -> tuple[ToolDefinition, ...]:
    """Return the immutable ordered diagnostic registry."""

    return TOOL_REGISTRY
