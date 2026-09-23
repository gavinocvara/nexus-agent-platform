import json
import logging

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nexus.observability.tracing import create_tracing_runtime
from nexus.web import JsonFormatter


def test_json_formatter_includes_request_metadata() -> None:
    record = logging.LogRecord(
        name="users",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request completed",
        args=(),
        exc_info=None,
    )
    record.service = "users"
    record.method = "GET"
    record.path = "/users/1"
    record.status_code = 200

    payload = json.loads(JsonFormatter().format(record))

    assert payload["service"] == "users"
    assert payload["method"] == "GET"
    assert payload["path"] == "/users/1"
    assert payload["status_code"] == 200
    assert "trace_id" not in payload
    assert "span_id" not in payload


def test_json_formatter_uses_real_active_trace_context() -> None:
    exporter = InMemorySpanExporter()
    runtime = create_tracing_runtime("test", True, "unused", exporter)
    tracer = runtime.tracer
    assert tracer is not None
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="inside span",
        args=(),
        exc_info=None,
    )

    with tracer.start_as_current_span("test-span"):
        payload = json.loads(JsonFormatter().format(record))

    assert len(payload["trace_id"]) == 32
    assert len(payload["span_id"]) == 16
    assert payload["trace_id"] != "0" * 32
    runtime.shutdown()
