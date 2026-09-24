"""Explicit OpenTelemetry tracing runtime and instrumentor adapters."""

from dataclasses import dataclass

import httpx
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from sqlalchemy import Engine


@dataclass(slots=True)
class TracingRuntime:
    """Own a service tracer provider and explicit client/database instrumentation."""

    provider: TracerProvider | None
    sqlalchemy_instrumented: bool = False

    @property
    def tracer(self) -> trace.Tracer | None:
        """Return this service's tracer when tracing is enabled."""

        return self.provider.get_tracer("nexus.http") if self.provider is not None else None

    def instrument_httpx(self, client: httpx.AsyncClient) -> None:
        """Instrument one Gateway HTTPX client without global monkey-patching."""

        if self.provider is not None:
            HTTPXClientInstrumentor.instrument_client(client, tracer_provider=self.provider)

    def uninstrument_httpx(self, client: httpx.AsyncClient) -> None:
        """Remove instrumentation from one client."""

        if self.provider is not None:
            HTTPXClientInstrumentor.uninstrument_client(client)

    def instrument_sqlalchemy(self, engine: Engine) -> None:
        """Instrument one Orders SQLAlchemy engine."""

        if self.provider is not None:
            SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=self.provider)
            self.sqlalchemy_instrumented = True

    def shutdown(self) -> None:
        """Flush and stop telemetry without affecting service correctness."""

        if self.provider is not None:
            if self.sqlalchemy_instrumented:
                SQLAlchemyInstrumentor().uninstrument()
                self.sqlalchemy_instrumented = False
            self.provider.shutdown()


def create_tracing_runtime(
    service: str,
    enabled: bool,
    endpoint: str,
    span_exporter: SpanExporter | None = None,
) -> TracingRuntime:
    """Create an isolated tracer provider or a disabled no-op runtime."""

    if not enabled:
        return TracingRuntime(provider=None)

    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    exporter = span_exporter or OTLPSpanExporter(endpoint=endpoint, insecure=True)
    if span_exporter is None:
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                schedule_delay_millis=500,
                export_timeout_millis=1000,
            )
        )
    else:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    return TracingRuntime(provider=provider)
