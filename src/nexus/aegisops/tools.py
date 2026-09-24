"""Fail-closed construction of the investigator's exact read-only SDK tools."""

import json
from collections.abc import Callable
from contextvars import ContextVar, Token
from hashlib import sha256
from typing import Annotated, Any, Literal

from agents import FunctionTool, RunContextWrapper, Tool, function_tool
from pydantic import BaseModel, Field

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
ToolBodyObserver = Callable[[str, str | None], None]
_TOOL_BODY_OBSERVER: ContextVar[ToolBodyObserver | None] = ContextVar(
    "nexus_tool_body_observer", default=None
)
ResultLimit = Annotated[int, Field(ge=1, le=100)]
HttpStatusCode = Annotated[int, Field(ge=100, le=599)]
SdkCorrelationId = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
]
SdkTraceId = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{32}$")]


class ToolBoundaryError(RuntimeError):
    """The registry, policy, or implementation no longer forms the approved boundary."""


class DiagnosticToolExecutionError(RuntimeError):
    """A sanitized wrapper for an unexpected diagnostic implementation failure."""


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
    payload = {
        "registry": [item.model_dump(mode="json") for item in get_tool_registry()],
        "sdk_tools": [
            {
                "name": tool.name,
                "params_json_schema": tool.params_json_schema,
                "strict_json_schema": tool.strict_json_schema,
                "allowed_callers": tool.allowed_callers,
                "output_json_schema": tool.output_json_schema,
            }
            for tool in SDK_TOOLS
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def execute_tool(
    context: InvestigatorContext,
    name: str,
    arguments: BaseModel | None = None,
) -> DiagnosticResult:
    """Dispatch only allowlisted methods on the existing diagnostic service layer."""

    if context.diagnostics.session is not context.session:
        raise ToolBoundaryError("Runtime context session does not own the diagnostic service")
    if name not in EXPECTED_TOOLS:
        raise ToolBoundaryError(f"Unapproved diagnostic tool: {name}")
    method = getattr(context.diagnostics, name, None)
    if not callable(method):
        raise ToolBoundaryError(f"Diagnostic service does not implement {name}")
    with context.session.reserve_call():
        try:
            result: Any = method() if arguments is None else method(arguments)
        except Exception as exc:
            raise DiagnosticToolExecutionError("Diagnostic tool execution failed") from exc
    if not isinstance(result, DiagnosticResult):
        raise ToolBoundaryError(f"Diagnostic tool {name} returned an invalid result")
    return result


def _serialized(
    context: RunContextWrapper[InvestigatorContext],
    name: str,
    arguments: BaseModel | None = None,
) -> str:
    observer = _TOOL_BODY_OBSERVER.get()
    if observer is not None:
        raw_call_id = getattr(context, "tool_call_id", None)
        observer(name, raw_call_id if isinstance(raw_call_id, str) else None)
    return execute_tool(context.context, name, arguments).model_dump_json()


def set_tool_body_observer(observer: ToolBodyObserver) -> Token[ToolBodyObserver | None]:
    """Install a payload-free SDK lifecycle observer for the current async context."""

    return _TOOL_BODY_OBSERVER.set(observer)


def reset_tool_body_observer(token: Token[ToolBodyObserver | None]) -> None:
    _TOOL_BODY_OBSERVER.reset(token)


@function_tool(failure_error_function=None, allowed_callers=["direct"])
def list_services(context: RunContextWrapper[InvestigatorContext]) -> str:
    """List visible operational topology."""
    return _serialized(context, "list_services")


@function_tool(failure_error_function=None, allowed_callers=["direct"])
def get_service_health(
    context: RunContextWrapper[InvestigatorContext], service: DiagnosticService
) -> str:
    """Read one registered component's health."""
    return _serialized(context, "get_service_health", ServiceInput(service=service))


@function_tool(failure_error_function=None, allowed_callers=["direct"])
def get_system_health(context: RunContextWrapper[InvestigatorContext]) -> str:
    """Read a bounded system health snapshot."""
    return _serialized(context, "get_system_health")


@function_tool(failure_error_function=None, allowed_callers=["direct"])
def get_request_summary(
    context: RunContextWrapper[InvestigatorContext],
    service: ApplicationService,
    window: DiagnosticWindow = DiagnosticWindow.FIVE_MINUTES,
) -> str:
    """Summarize bounded request metrics."""
    return _serialized(
        context, "get_request_summary", RequestSummaryInput(service=service, window=window)
    )


@function_tool(failure_error_function=None, allowed_callers=["direct"])
def get_dependency_summary(
    context: RunContextWrapper[InvestigatorContext],
    service: Literal[ApplicationService.GATEWAY],
    dependency: Literal[DiagnosticService.USERS, DiagnosticService.ORDERS],
    window: DiagnosticWindow = DiagnosticWindow.FIVE_MINUTES,
) -> str:
    """Summarize one registered dependency edge."""
    return _serialized(
        context,
        "get_dependency_summary",
        DependencySummaryInput(service=service, dependency=dependency, window=window),
    )


@function_tool(failure_error_function=None, allowed_callers=["direct"])
def get_database_health(context: RunContextWrapper[InvestigatorContext]) -> str:
    """Read the Orders PostgreSQL health metric."""
    return _serialized(context, "get_database_health", DatabaseHealthInput())


@function_tool(failure_error_function=None, allowed_callers=["direct"])
def search_logs(
    context: RunContextWrapper[InvestigatorContext],
    service: ApplicationService,
    window: DiagnosticWindow = DiagnosticWindow.FIVE_MINUTES,
    level: LogLevel | None = None,
    correlation_id: SdkCorrelationId | None = None,
    status_code: HttpStatusCode | None = None,
    limit: ResultLimit = 20,
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


@function_tool(failure_error_function=None, allowed_callers=["direct"])
def get_request_evidence(
    context: RunContextWrapper[InvestigatorContext], correlation_id: SdkCorrelationId
) -> str:
    """Find chronological logs for one exact correlation ID."""
    return _serialized(
        context, "get_request_evidence", CorrelationInput(correlation_id=correlation_id)
    )


@function_tool(failure_error_function=None, allowed_callers=["direct"])
def find_traces(
    context: RunContextWrapper[InvestigatorContext], correlation_id: SdkCorrelationId
) -> str:
    """Find trace IDs through exact correlation logs."""
    return _serialized(context, "find_traces", CorrelationInput(correlation_id=correlation_id))


@function_tool(failure_error_function=None, allowed_callers=["direct"])
def get_trace(context: RunContextWrapper[InvestigatorContext], trace_id: SdkTraceId) -> str:
    """Retrieve one exact normalized trace."""
    return _serialized(context, "get_trace", TraceInput(trace_id=trace_id))


@function_tool(failure_error_function=None, allowed_callers=["direct"])
def get_recent_errors(
    context: RunContextWrapper[InvestigatorContext],
    service: ApplicationService,
    window: DiagnosticWindow = DiagnosticWindow.FIVE_MINUTES,
    limit: ResultLimit = 20,
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
    for tool in SDK_TOOLS:
        if tool.output_json_schema is not None or tool._output_type_adapter is not None:
            raise ToolBoundaryError(
                f"String tool {tool.name} unexpectedly declares structured SDK output"
            )
    return list(SDK_TOOLS)


ToolInvoker = Callable[[InvestigatorContext, str, BaseModel | None], DiagnosticResult]
