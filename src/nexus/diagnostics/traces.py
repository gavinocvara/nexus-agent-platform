"""Exact Tempo trace retrieval with safe attribute normalization."""

from typing import Any, cast

import httpx

from nexus.diagnostics._http import BoundedJsonClient, DiagnosticBackendError
from nexus.diagnostics.config import DiagnosticsSettings
from nexus.diagnostics.models import SafeAttribute, TraceEvidence, TraceId, TraceSpan

SAFE_SPAN_ATTRIBUTES = frozenset(
    {
        "http.request.method",
        "http.route",
        "http.response.status_code",
        "db.system",
        "db.operation.name",
        "error.type",
        "server.address",
        "network.protocol.version",
    }
)


def _attribute_value(value: Any) -> SafeAttribute | None:
    if not isinstance(value, dict):
        return None
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in value and isinstance(value[key], str | int | float | bool):
            raw = value[key]
            if key == "intValue":
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    return None
            return cast(SafeAttribute, raw)
    return None


def _attributes(items: Any, allowed: frozenset[str]) -> dict[str, SafeAttribute]:
    if not isinstance(items, list):
        return {}
    normalized: dict[str, SafeAttribute] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("key") not in allowed:
            continue
        value = _attribute_value(item.get("value"))
        if value is not None:
            normalized[str(item["key"])] = value
    return normalized


def _service_name(resource: Any) -> str:
    if not isinstance(resource, dict):
        return "unknown"
    attributes = _attributes(resource.get("attributes"), frozenset({"service.name"}))
    return str(attributes.get("service.name", "unknown"))[:100]


def _nanoseconds(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise DiagnosticBackendError(f"Tempo returned an invalid {field}") from exc


def _operation(value: Any, attributes: dict[str, SafeAttribute]) -> str:
    """Keep useful names while replacing any database statement-shaped span name."""

    raw = str(value or "unknown")[:300]
    if "db.system" not in attributes:
        return raw
    operation = attributes.get("db.operation.name")
    if operation is None:
        candidate = raw.split(maxsplit=1)[0].upper()
        operation = candidate if candidate in {"SELECT", "INSERT", "UPDATE", "DELETE"} else "query"
    return f"database {str(operation).lower()}"


def normalize_trace(trace_id: TraceId, payload: dict[str, Any]) -> TraceEvidence:
    """Normalize OTLP JSON while dropping all non-allowlisted attributes."""

    batches = payload.get("batches")
    if not isinstance(batches, list):
        raise DiagnosticBackendError("Tempo returned malformed trace data")
    spans: list[TraceSpan] = []
    trace_start: int | None = None
    trace_end: int | None = None
    for batch in batches:
        if not isinstance(batch, dict):
            raise DiagnosticBackendError("Tempo returned malformed batch data")
        service = _service_name(batch.get("resource"))
        scope_spans = batch.get("scopeSpans", [])
        if not isinstance(scope_spans, list):
            raise DiagnosticBackendError("Tempo returned malformed scope data")
        for scope in scope_spans:
            raw_spans = scope.get("spans", []) if isinstance(scope, dict) else []
            if not isinstance(raw_spans, list):
                raise DiagnosticBackendError("Tempo returned malformed span data")
            for raw in raw_spans:
                if not isinstance(raw, dict):
                    raise DiagnosticBackendError("Tempo returned malformed span data")
                start = _nanoseconds(raw.get("startTimeUnixNano"), "span start")
                end = _nanoseconds(raw.get("endTimeUnixNano"), "span end")
                trace_start = start if trace_start is None else min(trace_start, start)
                trace_end = end if trace_end is None else max(trace_end, end)
                status = raw.get("status", {})
                status_code = (
                    status.get("code", "STATUS_CODE_UNSET")
                    if isinstance(status, dict)
                    else "STATUS_CODE_UNSET"
                )
                parent = str(raw.get("parentSpanId", "")) or None
                attributes = _attributes(raw.get("attributes"), SAFE_SPAN_ATTRIBUTES)
                spans.append(
                    TraceSpan(
                        span_id=str(raw.get("spanId", "")),
                        parent_span_id=parent,
                        service=service,
                        operation=_operation(raw.get("name"), attributes),
                        kind=str(raw.get("kind", "SPAN_KIND_UNSPECIFIED")),
                        duration_ms=max(0.0, (end - start) / 1_000_000),
                        status=str(status_code),
                        attributes=attributes,
                    )
                )
    if trace_start is None or trace_end is None:
        raise DiagnosticBackendError("Tempo trace contained no spans")
    return TraceEvidence(
        trace_id=str(trace_id).lower(),
        duration_ms=max(0.0, (trace_end - trace_start) / 1_000_000),
        services=sorted({span.service for span in spans}),
        spans=sorted(spans, key=lambda span: (span.service, span.operation, span.span_id)),
    )


class TempoAdapter:
    """Retrieve one exact trace ID; unrestricted trace search is absent."""

    def __init__(
        self,
        settings: DiagnosticsSettings,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = BoundedJsonClient(
            str(settings.tempo_url),
            settings.timeout_seconds,
            settings.max_response_bytes,
            transport,
        )

    def get(self, trace_id: TraceId) -> TraceEvidence:
        """Return a normalized exact trace."""

        payload = self._client.get_json(f"/api/traces/{trace_id}")
        return normalize_trace(trace_id, payload)

    def close(self) -> None:
        """Close the Tempo connection pool."""

        self._client.close()
