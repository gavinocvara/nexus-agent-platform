"""High-level read-only diagnostic operations for a future investigator."""

import re
from time import perf_counter
from typing import TypeVar

from pydantic import BaseModel

from nexus.diagnostics._http import DiagnosticBackendError
from nexus.diagnostics.audit import DiagnosticAuditEvent, DiagnosticSession
from nexus.diagnostics.config import DiagnosticsSettings
from nexus.diagnostics.health import HealthAdapter
from nexus.diagnostics.logs import LokiAdapter
from nexus.diagnostics.metrics import PrometheusAdapter
from nexus.diagnostics.models import (
    CorrelationInput,
    DatabaseHealthInput,
    DatabaseHealthResult,
    DependencySummaryInput,
    DependencySummaryResult,
    DiagnosticResult,
    DiagnosticService,
    DiagnosticSource,
    HealthState,
    LogSearchInput,
    LogSearchResult,
    NoArguments,
    RecentErrorsInput,
    RequestSummaryInput,
    RequestSummaryResult,
    ServiceDescriptor,
    ServiceHealthResult,
    ServiceInput,
    ServiceInventoryResult,
    SystemHealthResult,
    TraceInput,
    TraceResult,
    TraceSearchResult,
    json_safe_arguments,
)
from nexus.diagnostics.traces import TempoAdapter

ResultT = TypeVar("ResultT", bound=DiagnosticResult)
TRACE_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")

VISIBLE_TOPOLOGY = (
    ServiceDescriptor(
        service=DiagnosticService.GATEWAY,
        kind="http_service",
        dependencies=[DiagnosticService.USERS, DiagnosticService.ORDERS],
    ),
    ServiceDescriptor(service=DiagnosticService.USERS, kind="http_service"),
    ServiceDescriptor(
        service=DiagnosticService.ORDERS,
        kind="http_service",
        dependencies=[DiagnosticService.POSTGRES],
    ),
    ServiceDescriptor(service=DiagnosticService.POSTGRES, kind="database"),
)


class DiagnosticServiceLayer:
    """Only supported entry point to bounded operational evidence."""

    def __init__(
        self,
        settings: DiagnosticsSettings | None = None,
        session: DiagnosticSession | None = None,
        health: HealthAdapter | None = None,
        metrics: PrometheusAdapter | None = None,
        logs: LokiAdapter | None = None,
        traces: TempoAdapter | None = None,
    ) -> None:
        resolved = settings or DiagnosticsSettings()
        self.session = session or DiagnosticSession()
        self._health = health or HealthAdapter(resolved)
        self._metrics = metrics or PrometheusAdapter(resolved)
        self._logs = logs or LokiAdapter(resolved)
        self._traces = traces or TempoAdapter(resolved)

    def _finish(
        self,
        result: ResultT,
        arguments: BaseModel,
        started: float,
        result_count: int,
    ) -> ResultT:
        self.session.record(
            DiagnosticAuditEvent(
                session_id=self.session.session_id,
                tool=result.tool,
                normalized_arguments=json_safe_arguments(arguments),
                success=result.success,
                duration_ms=(perf_counter() - started) * 1000,
                backend=result.source,
                result_count=result_count,
            )
        )
        return result

    def list_services(self) -> ServiceInventoryResult:
        """List only ordinary operational topology."""

        started = perf_counter()
        arguments = NoArguments()
        result = ServiceInventoryResult(
            tool="list_services",
            source=DiagnosticSource.TOPOLOGY,
            services=list(VISIBLE_TOPOLOGY),
        )
        return self._finish(result, arguments, started, len(result.services))

    def get_service_health(self, arguments: ServiceInput) -> ServiceHealthResult:
        """Read one registered component's fixed health endpoint."""

        started = perf_counter()
        health = self._health.get(arguments.service)
        success = health.status is not HealthState.UNAVAILABLE
        result = ServiceHealthResult(
            tool="get_service_health",
            source=DiagnosticSource.SERVICE_HEALTH,
            success=success,
            warnings=[] if success else ["Service health backend unavailable"],
            health=health,
        )
        return self._finish(result, arguments, started, 1)

    def get_system_health(self) -> SystemHealthResult:
        """Read all registered component health with a fixed upper bound."""

        started = perf_counter()
        arguments = NoArguments()
        health = [self._health.get(item.service) for item in VISIBLE_TOPOLOGY]
        success = all(item.status is not HealthState.UNAVAILABLE for item in health)
        result = SystemHealthResult(
            tool="get_system_health",
            source=DiagnosticSource.SERVICE_HEALTH,
            success=success,
            warnings=[] if success else ["One or more service health backends unavailable"],
            services=health,
        )
        return self._finish(result, arguments, started, len(health))

    def get_request_summary(self, arguments: RequestSummaryInput) -> RequestSummaryResult:
        """Read predefined request aggregates without exposing PromQL."""

        started = perf_counter()
        try:
            summary = self._metrics.request_summary(arguments)
            result = RequestSummaryResult(
                tool="get_request_summary",
                source=DiagnosticSource.PROMETHEUS,
                summary=summary,
            )
            count = 1
        except DiagnosticBackendError:
            result = RequestSummaryResult(
                tool="get_request_summary",
                source=DiagnosticSource.PROMETHEUS,
                success=False,
                warnings=["Prometheus unavailable or returned invalid data"],
            )
            count = 0
        return self._finish(result, arguments, started, count)

    def get_dependency_summary(self, arguments: DependencySummaryInput) -> DependencySummaryResult:
        """Read predefined aggregates for one allowlisted dependency edge."""

        started = perf_counter()
        try:
            summary = self._metrics.dependency_summary(arguments)
            result = DependencySummaryResult(
                tool="get_dependency_summary",
                source=DiagnosticSource.PROMETHEUS,
                summary=summary,
            )
            count = 1
        except DiagnosticBackendError:
            result = DependencySummaryResult(
                tool="get_dependency_summary",
                source=DiagnosticSource.PROMETHEUS,
                success=False,
                warnings=["Prometheus unavailable or returned invalid data"],
            )
            count = 0
        return self._finish(result, arguments, started, count)

    def get_database_health(
        self, arguments: DatabaseHealthInput | None = None
    ) -> DatabaseHealthResult:
        """Read only the fixed Orders/PostgreSQL health gauge."""

        started = perf_counter()
        request = arguments or DatabaseHealthInput()
        try:
            healthy = self._metrics.database_health()
            result = DatabaseHealthResult(
                tool="get_database_health",
                source=DiagnosticSource.PROMETHEUS,
                healthy=healthy,
                warnings=[] if healthy is not None else ["Database health metric has no sample"],
            )
            count = int(healthy is not None)
        except DiagnosticBackendError:
            result = DatabaseHealthResult(
                tool="get_database_health",
                source=DiagnosticSource.PROMETHEUS,
                success=False,
                warnings=["Prometheus unavailable or returned invalid data"],
            )
            count = 0
        return self._finish(result, request, started, count)

    def search_logs(self, arguments: LogSearchInput) -> LogSearchResult:
        """Retrieve logs using only approved exact structured filters."""

        started = perf_counter()
        try:
            events = self._logs.search(arguments)
            result = LogSearchResult(
                tool="search_logs",
                source=DiagnosticSource.LOKI,
                events=events,
            )
        except DiagnosticBackendError:
            result = LogSearchResult(
                tool="search_logs",
                source=DiagnosticSource.LOKI,
                success=False,
                warnings=["Loki unavailable or returned invalid data"],
                events=[],
            )
        return self._finish(result, arguments, started, len(result.events))

    def get_request_evidence(self, arguments: CorrelationInput) -> LogSearchResult:
        """Retrieve chronological cross-service logs for one exact correlation ID."""

        started = perf_counter()
        try:
            events = self._logs.correlation(arguments.correlation_id)
            result = LogSearchResult(
                tool="get_request_evidence",
                source=DiagnosticSource.LOKI,
                events=events,
            )
        except DiagnosticBackendError:
            result = LogSearchResult(
                tool="get_request_evidence",
                source=DiagnosticSource.LOKI,
                success=False,
                warnings=["Loki unavailable or returned invalid data"],
                events=[],
            )
        return self._finish(result, arguments, started, len(result.events))

    def get_trace(self, arguments: TraceInput) -> TraceResult:
        """Retrieve one exact trace and return only allowlisted fields."""

        started = perf_counter()
        try:
            trace = self._traces.get(arguments.trace_id)
            result = TraceResult(
                tool="get_trace",
                source=DiagnosticSource.TEMPO,
                trace=trace,
            )
            count = len(trace.spans)
        except DiagnosticBackendError:
            result = TraceResult(
                tool="get_trace",
                source=DiagnosticSource.TEMPO,
                success=False,
                warnings=["Tempo unavailable or returned invalid data"],
            )
            count = 0
        return self._finish(result, arguments, started, count)

    def find_traces(self, arguments: CorrelationInput) -> TraceSearchResult:
        """Find bounded unique trace IDs through exact correlation log evidence."""

        started = perf_counter()
        try:
            events = self._logs.correlation(arguments.correlation_id)
            trace_ids = sorted(
                {
                    event.trace_id.lower()
                    for event in events
                    if event.trace_id is not None and TRACE_ID_PATTERN.fullmatch(event.trace_id)
                }
            )[:10]
            result = TraceSearchResult(
                tool="find_traces",
                source=DiagnosticSource.LOKI,
                trace_ids=trace_ids,
            )
        except DiagnosticBackendError:
            result = TraceSearchResult(
                tool="find_traces",
                source=DiagnosticSource.LOKI,
                success=False,
                warnings=["Loki unavailable or returned invalid data"],
                trace_ids=[],
            )
        return self._finish(result, arguments, started, len(result.trace_ids))

    def get_recent_errors(self, arguments: RecentErrorsInput) -> LogSearchResult:
        """Retrieve structured 5xx events without forming a diagnosis."""

        started = perf_counter()
        try:
            events = self._logs.recent_errors(
                arguments.service,
                arguments.window,
                arguments.limit,
            )
            result = LogSearchResult(
                tool="get_recent_errors",
                source=DiagnosticSource.LOKI,
                events=events,
            )
        except DiagnosticBackendError:
            result = LogSearchResult(
                tool="get_recent_errors",
                source=DiagnosticSource.LOKI,
                success=False,
                warnings=["Loki unavailable or returned invalid data"],
                events=[],
            )
        return self._finish(result, arguments, started, len(result.events))

    def close(self) -> None:
        """Close all diagnostic backend connection pools."""

        self._health.close()
        self._metrics.close()
        self._logs.close()
        self._traces.close()

    def __enter__(self) -> "DiagnosticServiceLayer":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
