"""Install test-friendly metrics and tracing on FastAPI applications."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from fastapi import FastAPI, Request
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer
from sqlalchemy import Engine
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from nexus.config import NexusSettings
from nexus.observability.metrics import ServiceMetrics, install_metrics_endpoint, normalized_route
from nexus.observability.tracing import TracingRuntime, create_tracing_runtime


class TelemetryMiddleware(BaseHTTPMiddleware):
    """Record normalized incoming request metrics and server spans."""

    def __init__(
        self,
        app: Any,
        service: str,
        metrics: ServiceMetrics | None,
        tracer: Tracer | None,
    ) -> None:
        super().__init__(app)
        self.service = service
        self.metrics = metrics
        self.tracer = tracer

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if self.metrics is not None:
            self.metrics.http_in_flight.labels(self.service).inc()
        started = perf_counter()
        response_status = 500
        context = propagate.extract(dict(request.headers))
        span_manager = (
            self.tracer.start_as_current_span(
                request.method,
                context=context,
                kind=SpanKind.SERVER,
            )
            if self.tracer is not None
            else None
        )
        try:
            if span_manager is None:
                response = await call_next(request)
            else:
                with span_manager as span:
                    response = await call_next(request)
                    response_status = response.status_code
                    route = normalized_route(request)
                    span.update_name(f"{request.method} {route}")
                    span.set_attribute("http.request.method", request.method)
                    span.set_attribute("http.route", route)
                    span.set_attribute("http.response.status_code", response_status)
                    if request.url.hostname is not None:
                        span.set_attribute("server.address", request.url.hostname)
                    http_version = request.scope.get("http_version")
                    if isinstance(http_version, str):
                        span.set_attribute("network.protocol.version", http_version)
                    if response_status >= 500:
                        span.set_status(Status(StatusCode.ERROR))
            response_status = response.status_code
            return response
        except Exception as exc:
            current_span = trace.get_current_span()
            if current_span.is_recording():
                current_span.record_exception(exc)
                current_span.set_status(Status(StatusCode.ERROR))
            raise
        finally:
            duration = perf_counter() - started
            if self.metrics is not None:
                self.metrics.http_in_flight.labels(self.service).dec()
                self.metrics.observe_http(
                    request.method,
                    normalized_route(request),
                    response_status,
                    duration,
                )


@dataclass(slots=True)
class ObservabilityRuntime:
    """Metrics and tracing resources owned by one service app."""

    metrics: ServiceMetrics | None
    tracing: TracingRuntime

    def shutdown(self) -> None:
        """Flush tracing resources during graceful service shutdown."""

        self.tracing.shutdown()


def install_observability(
    app: FastAPI,
    settings: NexusSettings,
    service: str,
    engine: Engine | None = None,
) -> ObservabilityRuntime:
    """Explicitly install configured telemetry and return owned resources."""

    metrics = ServiceMetrics(service) if settings.metrics_enabled else None
    if metrics is not None:
        install_metrics_endpoint(app, metrics)
    tracing = create_tracing_runtime(
        service,
        settings.otel_enabled,
        settings.otel_exporter_endpoint,
    )
    if engine is not None:
        tracing.instrument_sqlalchemy(engine)
    app.add_middleware(
        TelemetryMiddleware,
        service=service,
        metrics=metrics,
        tracer=tracing.tracer,
    )
    return ObservabilityRuntime(metrics=metrics, tracing=tracing)
