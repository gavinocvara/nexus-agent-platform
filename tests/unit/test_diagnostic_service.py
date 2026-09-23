import json

import httpx

from nexus.diagnostics.audit import DiagnosticSession
from nexus.diagnostics.config import DiagnosticsSettings
from nexus.diagnostics.logs import LokiAdapter
from nexus.diagnostics.metrics import PrometheusAdapter
from nexus.diagnostics.models import CorrelationInput, RequestSummaryInput
from nexus.diagnostics.service import DiagnosticServiceLayer


def _settings() -> DiagnosticsSettings:
    return DiagnosticsSettings(_env_file=None)


def test_backend_unavailability_becomes_typed_failure_and_safe_audit() -> None:
    session = DiagnosticSession()
    metrics = PrometheusAdapter(
        _settings(),
        httpx.MockTransport(lambda _request: httpx.Response(503, json={"secret": "no"})),
    )
    layer = object.__new__(DiagnosticServiceLayer)
    layer.session = session
    layer._metrics = metrics

    result = layer.get_request_summary(RequestSummaryInput(service="orders", window="5m"))
    metrics.close()
    assert result.success is False
    assert result.summary is None
    assert result.warnings == ["Prometheus unavailable or returned invalid data"]
    assert session.tool_call_count == 1
    event = session.audit_records()[0]
    assert event.tool == "get_request_summary"
    assert event.result_count == 0
    serialized = event.model_dump_json()
    assert "secret" not in serialized
    assert "evidence" not in serialized


def test_correlation_to_trace_ids_is_bounded_unique_and_audited() -> None:
    trace_ids = [f"{number:032x}" for number in range(12)]
    values = []
    for index, trace_id in enumerate(trace_ids + [trace_ids[0]]):
        line = json.dumps(
            {
                "service": "gateway",
                "level": "info",
                "message": "request completed",
                "correlation_id": "request-123",
                "trace_id": trace_id,
            }
        )
        values.append([str(1_000_000_000 + index), line])
    payload = {"status": "success", "data": {"result": [{"values": values}]}}
    logs = LokiAdapter(
        _settings(), httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    )
    layer = object.__new__(DiagnosticServiceLayer)
    layer.session = DiagnosticSession()
    layer._logs = logs

    result = layer.find_traces(CorrelationInput(correlation_id="request-123"))
    logs.close()
    assert result.success is True
    assert len(result.trace_ids) == 10
    assert len(set(result.trace_ids)) == 10
    assert layer.session.tool_call_count == 1
    assert layer.session.audit_records()[0].normalized_arguments == {
        "correlation_id": "request-123"
    }
