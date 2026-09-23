"""Fail-closed construction of the investigator's exact read-only SDK tools."""

import json
from collections.abc import Callable
from hashlib import sha256
from typing import Any

from agents import FunctionTool, RunContextWrapper, Tool, function_tool
from pydantic import BaseModel

from nexus.aegisops.context import InvestigatorContext
from nexus.diagnostics.models import (
    ApplicationService,
    CorrelationInput,
    DatabaseHealthInput,
    DependencySummaryInput,
    DiagnosticResult,
    DiagnosticService,
    DiagnosticWindow,
    LogLevel,
    LogSearchInput,
    RecentErrorsInput,
    RequestSummaryInput,
    ServiceInput,
    TraceInput,
)
from nexus.diagnostics.policy import INVESTIGATOR_POLICY
from nexus.diagnostics.registry import get_tool_registry

EXPECTED_TOOLS = frozenset(INVESTIGATOR_POLICY.allowed_tools)


class ToolBoundaryError(RuntimeError):
    """The registry, policy, or implementation no longer forms the approved boundary."""


def validate_tool_boundary(implementation_names: set[str]) -> None:
    """Reject drift, writable tools, elevated risk, duplicates, and policy conflicts."""

    registry = get_tool_registry()
    names = [item.name for item in registry]
    if len(names) != len(set(names)):
        raise ToolBoundaryError("Diagnostic registry contains duplicate tool names")
    if set(names) != EXPECTED_TOOLS or implementation_names != EXPECTED_TOOLS:
        raise ToolBoundaryError("Tool registry, policy, and implementation do not match")
    if not INVESTIGATOR_POLICY.read_only:
        raise ToolBoundaryError("Investigator policy is not read-only")
    if any(not item.read_only or item.risk != "low" for item in registry):
        raise ToolBoundaryError("Investigator tool has writable or unexpected risk metadata")


def tool_registry_hash() -> str:
    payload = [item.model_dump(mode="json") for item in get_tool_registry()]
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def execute_tool(
    context: InvestigatorContext,
    name: str,
    arguments: BaseModel | None = None,
) -> DiagnosticResult:
    """Dispatch only allowlisted methods on the existing diagnostic service layer."""

    context.session.assert_call_available()
    if context.diagnostics.session is not context.session:
        raise ToolBoundaryError("Runtime context session does not own the diagnostic service")
    if name not in EXPECTED_TOOLS:
        raise ToolBoundaryError(f"Unapproved diagnostic tool: {name}")
    method = getattr(context.diagnostics, name, None)
    if not callable(method):
        raise ToolBoundaryError(f"Diagnostic service does not implement {name}")
    result: Any = method() if arguments is None else method(arguments)
    if not isinstance(result, DiagnosticResult):
        raise ToolBoundaryError(f"Diagnostic tool {name} returned an invalid result")
    return result


def _serialized(
    context: RunContextWrapper[InvestigatorContext],
    name: str,
    arguments: BaseModel | None = None,
) -> str:
    return execute_tool(context.context, name, arguments).model_dump_json()


@function_tool(failure_error_function=None)
def list_services(context: RunContextWrapper[InvestigatorContext]) -> str:
    """List visible operational topology."""
    return _serialized(context, "list_services")


@function_tool(failure_error_function=None)
def get_service_health(
    context: RunContextWrapper[InvestigatorContext], service: DiagnosticService
) -> str:
    """Read one registered component's health."""
    return _serialized(context, "get_service_health", ServiceInput(service=service))


@function_tool(failure_error_function=None)
def get_system_health(context: RunContextWrapper[InvestigatorContext]) -> str:
    """Read a bounded system health snapshot."""
    return _serialized(context, "get_system_health")


@function_tool(failure_error_function=None)
def get_request_summary(
    context: RunContextWrapper[InvestigatorContext],
    service: ApplicationService,
    window: DiagnosticWindow = DiagnosticWindow.FIVE_MINUTES,
) -> str:
    """Summarize bounded request metrics."""
    return _serialized(
        context, "get_request_summary", RequestSummaryInput(service=service, window=window)
    )


@function_tool(failure_error_function=None)
def get_dependency_summary(
    context: RunContextWrapper[InvestigatorContext],
    service: ApplicationService,
    dependency: DiagnosticService,
    window: DiagnosticWindow = DiagnosticWindow.FIVE_MINUTES,
) -> str:
    """Summarize one registered dependency edge."""
    return _serialized(
        context,
        "get_dependency_summary",
        DependencySummaryInput(service=service, dependency=dependency, window=window),
    )


@function_tool(failure_error_function=None)
def get_database_health(context: RunContextWrapper[InvestigatorContext]) -> str:
    """Read the Orders PostgreSQL health metric."""
    return _serialized(context, "get_database_health", DatabaseHealthInput())


@function_tool(failure_error_function=None)
def search_logs(
    context: RunContextWrapper[InvestigatorContext],
    service: ApplicationService,
    window: DiagnosticWindow = DiagnosticWindow.FIVE_MINUTES,
    level: LogLevel | None = None,
    correlation_id: str | None = None,
    status_code: int | None = None,
    limit: int = 20,
) -> str:
    """Search logs with approved structured filters."""
    return _serialized(
        context,
        "search_logs",
        LogSearchInput(
            service=service,
            window=window,
            level=level,
            correlation_id=correlation_id,
            status_code=status_code,
            limit=limit,
        ),
    )


@function_tool(failure_error_function=None)
def get_request_evidence(
    context: RunContextWrapper[InvestigatorContext], correlation_id: str
) -> str:
    """Find chronological logs for one exact correlation ID."""
    return _serialized(
        context, "get_request_evidence", CorrelationInput(correlation_id=correlation_id)
    )


@function_tool(failure_error_function=None)
def find_traces(context: RunContextWrapper[InvestigatorContext], correlation_id: str) -> str:
    """Find trace IDs through exact correlation logs."""
    return _serialized(context, "find_traces", CorrelationInput(correlation_id=correlation_id))


@function_tool(failure_error_function=None)
def get_trace(context: RunContextWrapper[InvestigatorContext], trace_id: str) -> str:
    """Retrieve one exact normalized trace."""
    return _serialized(context, "get_trace", TraceInput(trace_id=trace_id))


@function_tool(failure_error_function=None)
def get_recent_errors(
    context: RunContextWrapper[InvestigatorContext],
    service: ApplicationService,
    window: DiagnosticWindow = DiagnosticWindow.FIVE_MINUTES,
    limit: int = 20,
) -> str:
    """Retrieve bounded recent error events."""
    return _serialized(
        context,
        "get_recent_errors",
        RecentErrorsInput(service=service, window=window, limit=limit),
    )


SDK_TOOLS: tuple[FunctionTool, ...] = (
    list_services,
    get_service_health,
    get_system_health,
    get_request_summary,
    get_dependency_summary,
    get_database_health,
    search_logs,
    get_request_evidence,
    find_traces,
    get_trace,
    get_recent_errors,
)


def build_sdk_tools() -> list[Tool]:
    implementations = {tool.name for tool in SDK_TOOLS}
    validate_tool_boundary(implementations)
    return list(SDK_TOOLS)


ToolInvoker = Callable[[InvestigatorContext, str, BaseModel | None], DiagnosticResult]
