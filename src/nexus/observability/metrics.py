"""Low-cardinality Prometheus metrics for NEXUS services."""

from time import perf_counter

from fastapi import FastAPI, Request
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.exposition import generate_latest
from starlette.responses import Response

HTTP_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def normalized_route(request: Request) -> str:
    """Return a bounded route template and never a raw identifier-bearing URL."""

    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    return route_path if isinstance(route_path, str) else "unmatched"


def status_class(status_code: int) -> str:
    """Convert an HTTP status into one of five bounded label values."""

    return f"{status_code // 100}xx"


class ServiceMetrics:
    """Per-process Prometheus registry and stable NEXUS metric set."""

    def __init__(self, service: str) -> None:
        self.service = service
        self.registry = CollectorRegistry()
        http_labels = ("service", "method", "route", "status_class")
        self.http_requests = Counter(
            "nexus_http_requests_total",
            "Completed HTTP requests.",
            http_labels,
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "nexus_http_request_duration_seconds",
            "HTTP request duration in seconds.",
            http_labels,
            buckets=HTTP_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.http_in_flight = Gauge(
            "nexus_http_requests_in_flight",
            "HTTP requests currently in flight.",
            ("service",),
            registry=self.registry,
        )
        dependency_labels = ("service", "dependency", "status_class")
        self.dependency_requests = Counter(
            "nexus_dependency_requests_total",
            "Completed downstream dependency requests.",
            dependency_labels,
            registry=self.registry,
        )
        self.dependency_failures = Counter(
            "nexus_dependency_failures_total",
            "Failed downstream dependency requests.",
            dependency_labels,
            registry=self.registry,
        )
        self.dependency_duration = Histogram(
            "nexus_dependency_request_duration_seconds",
            "Downstream dependency request duration in seconds.",
            dependency_labels,
            buckets=HTTP_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.database_health = Gauge(
            "nexus_database_health",
            "Database dependency health where 1 is healthy and 0 is unhealthy.",
            ("service", "dependency"),
            registry=self.registry,
        )

    def observe_http(
        self,
        method: str,
        route: str,
        response_status: int,
        duration_seconds: float,
    ) -> None:
        """Record one completed incoming request."""

        labels = (self.service, method, route, status_class(response_status))
        self.http_requests.labels(*labels).inc()
        self.http_duration.labels(*labels).observe(duration_seconds)

    def observe_dependency(
        self,
        dependency: str,
        response_status: int | None,
        duration_seconds: float,
    ) -> None:
        """Record one bounded downstream call and failure state."""

        response_class = status_class(response_status) if response_status is not None else "error"
        labels = (self.service, dependency, response_class)
        self.dependency_requests.labels(*labels).inc()
        self.dependency_duration.labels(*labels).observe(duration_seconds)
        if response_status is None or response_status >= 500:
            self.dependency_failures.labels(*labels).inc()

    def set_database_health(self, healthy: bool) -> None:
        """Set the low-cardinality Orders database health signal."""

        self.database_health.labels(self.service, "postgres").set(1 if healthy else 0)

    def render(self) -> bytes:
        """Render the registry in Prometheus exposition format."""

        return generate_latest(self.registry)


def install_metrics_endpoint(app: FastAPI, metrics: ServiceMetrics) -> None:
    """Expose one Prometheus scrape endpoint on a service app."""

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> Response:
        return Response(metrics.render(), media_type=CONTENT_TYPE_LATEST)


class DependencyTimer:
    """Measure a Gateway dependency call and record it exactly once."""

    def __init__(self, metrics: ServiceMetrics | None, dependency: str) -> None:
        self.metrics = metrics
        self.dependency = dependency
        self.started = perf_counter()

    def record(self, response_status: int | None) -> None:
        """Record elapsed time when metrics are enabled."""

        if self.metrics is not None:
            self.metrics.observe_dependency(
                self.dependency,
                response_status,
                perf_counter() - self.started,
            )
