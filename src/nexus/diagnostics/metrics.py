"""Predefined Prometheus query builders and result normalization."""

import math

import httpx

from nexus.diagnostics._http import BoundedJsonClient, DiagnosticBackendError
from nexus.diagnostics.config import DiagnosticsSettings
from nexus.diagnostics.models import (
    DependencySummary,
    DependencySummaryInput,
    DiagnosticWindow,
    RequestSummary,
    RequestSummaryInput,
)


def _rate(metric: str, labels: str, window: DiagnosticWindow) -> str:
    return f"sum(rate({metric}{{{labels}}}[{window.value}]))"


def _quantile(metric: str, labels: str, window: DiagnosticWindow, quantile: float) -> str:
    return (
        f"histogram_quantile({quantile}, sum by (le) "
        f"(rate({metric}_bucket{{{labels}}}[{window.value}])))"
    )


def build_request_queries(request: RequestSummaryInput) -> dict[str, str]:
    """Build the complete fixed query set from typed bounded inputs."""

    labels = f'service="{request.service.value}"'
    error_labels = f'{labels},status_class=~"4xx|5xx"'
    duration = "nexus_http_request_duration_seconds"
    return {
        "request_rate_per_second": _rate("nexus_http_requests_total", labels, request.window),
        "error_rate_per_second": _rate("nexus_http_requests_total", error_labels, request.window),
        "latency_p50_ms": _quantile(duration, labels, request.window, 0.5),
        "latency_p95_ms": _quantile(duration, labels, request.window, 0.95),
        "latency_p99_ms": _quantile(duration, labels, request.window, 0.99),
        "in_flight": f"sum(nexus_http_requests_in_flight{{{labels}}})",
    }


def build_dependency_queries(request: DependencySummaryInput) -> dict[str, str]:
    """Build fixed queries for one allowlisted topology edge."""

    labels = f'service="{request.service.value}",dependency="{request.dependency.value}"'
    duration = "nexus_dependency_request_duration_seconds"
    return {
        "request_rate_per_second": _rate("nexus_dependency_requests_total", labels, request.window),
        "failure_rate_per_second": _rate("nexus_dependency_failures_total", labels, request.window),
        "latency_p50_ms": _quantile(duration, labels, request.window, 0.5),
        "latency_p95_ms": _quantile(duration, labels, request.window, 0.95),
        "latency_p99_ms": _quantile(duration, labels, request.window, 0.99),
    }


class PrometheusAdapter:
    """Execute adapter-owned PromQL and return scalar values only."""

    def __init__(
        self,
        settings: DiagnosticsSettings,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = BoundedJsonClient(
            str(settings.prometheus_url),
            settings.timeout_seconds,
            settings.max_response_bytes,
            transport,
        )

    def _query(self, expression: str) -> float | None:
        payload = self._client.get_json("/api/v1/query", {"query": expression})
        if payload.get("status") != "success":
            raise DiagnosticBackendError("Prometheus query failed")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("result"), list):
            raise DiagnosticBackendError("Prometheus returned malformed query data")
        results = data["result"]
        if not results:
            return None
        first = results[0]
        if not isinstance(first, dict):
            raise DiagnosticBackendError("Prometheus returned malformed series data")
        value = first.get("value")
        if not isinstance(value, list) or len(value) != 2:
            raise DiagnosticBackendError("Prometheus returned malformed sample data")
        try:
            normalized = float(value[1])
        except (TypeError, ValueError) as exc:
            raise DiagnosticBackendError("Prometheus returned a non-numeric sample") from exc
        return normalized if math.isfinite(normalized) else None

    def request_summary(self, request: RequestSummaryInput) -> RequestSummary:
        """Return one bounded application request summary."""

        values = {
            name: self._query(query) for name, query in build_request_queries(request).items()
        }
        for key in ("latency_p50_ms", "latency_p95_ms", "latency_p99_ms"):
            value = values[key]
            if value is not None:
                values[key] = value * 1000
        return RequestSummary(service=request.service, window=request.window, **values)

    def dependency_summary(self, request: DependencySummaryInput) -> DependencySummary:
        """Return one bounded dependency summary."""

        values = {
            name: self._query(query) for name, query in build_dependency_queries(request).items()
        }
        for key in ("latency_p50_ms", "latency_p95_ms", "latency_p99_ms"):
            value = values[key]
            if value is not None:
                values[key] = value * 1000
        return DependencySummary(
            service=request.service,
            dependency=request.dependency,
            window=request.window,
            **values,
        )

    def database_health(self) -> bool | None:
        """Return the fixed Orders/PostgreSQL health gauge."""

        value = self._query('nexus_database_health{service="orders",dependency="postgres"}')
        return None if value is None else value >= 0.5

    def close(self) -> None:
        """Close the Prometheus connection pool."""

        self._client.close()
