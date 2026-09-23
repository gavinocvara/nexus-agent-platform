"""Controlled Loki query construction and structured log normalization."""

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from nexus.diagnostics._http import BoundedJsonClient, DiagnosticBackendError
from nexus.diagnostics.config import DiagnosticsSettings
from nexus.diagnostics.models import (
    ApplicationService,
    CorrelationId,
    DiagnosticWindow,
    LogEvent,
    LogSearchInput,
)


def build_log_query(request: LogSearchInput) -> str:
    """Build LogQL solely from validated structured fields."""

    query = f'{{service="{request.service.value}"}} | json'
    if request.level is not None:
        query += f' | level="{request.level.value}"'
    if request.correlation_id is not None:
        query += f' | correlation_id="{request.correlation_id}"'
    if request.status_code is not None:
        query += f" | status_code={request.status_code}"
    return query


def build_correlation_query(correlation_id: CorrelationId) -> str:
    """Build a fixed cross-service exact-correlation query."""

    return f'{{service=~"gateway|users|orders"}} | json | correlation_id="{correlation_id}"'


def build_recent_errors_query(service: ApplicationService) -> str:
    """Build a fixed structured 5xx query."""

    return f'{{service="{service.value}"}} | json | status_code >= 500'


def _timestamp(nanoseconds: str) -> datetime:
    try:
        return datetime.fromtimestamp(int(nanoseconds) / 1_000_000_000, tz=UTC)
    except (ValueError, OSError, OverflowError) as exc:
        raise DiagnosticBackendError("Loki returned an invalid timestamp") from exc


def _optional_string(payload: dict[str, Any], key: str, maximum: int) -> str | None:
    value = payload.get(key)
    return str(value)[:maximum] if value is not None else None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _event(nanoseconds: str, line: str) -> LogEvent | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        service = ApplicationService(str(payload.get("service")))
    except ValueError:
        return None
    status = payload.get("status_code")
    duration = payload.get("duration_ms")
    return LogEvent(
        timestamp=_timestamp(nanoseconds),
        service=service,
        level=str(payload.get("level", "unknown"))[:20],
        message=str(payload.get("message", ""))[:2000],
        correlation_id=_optional_string(payload, "correlation_id", 128),
        trace_id=_optional_string(payload, "trace_id", 32),
        span_id=_optional_string(payload, "span_id", 16),
        method=_optional_string(payload, "method", 16),
        path=_optional_string(payload, "path", 500),
        status_code=_optional_int(status),
        duration_ms=_optional_float(duration),
    )


class LokiAdapter:
    """Execute only generated Loki queries and return allowlisted event fields."""

    def __init__(
        self,
        settings: DiagnosticsSettings,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = BoundedJsonClient(
            str(settings.loki_url),
            settings.timeout_seconds,
            settings.max_response_bytes,
            transport,
        )

    def _query(self, query: str, window: DiagnosticWindow, limit: int) -> list[LogEvent]:
        payload = self._client.get_json(
            "/loki/api/v1/query_range",
            {"query": query, "since": window.value, "limit": str(limit), "direction": "backward"},
        )
        if payload.get("status") != "success":
            raise DiagnosticBackendError("Loki query failed")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("result"), list):
            raise DiagnosticBackendError("Loki returned malformed query data")
        events: list[LogEvent] = []
        for stream in data["result"]:
            if not isinstance(stream, dict) or not isinstance(stream.get("values"), list):
                raise DiagnosticBackendError("Loki returned malformed stream data")
            for value in stream["values"]:
                if not isinstance(value, list) or len(value) != 2:
                    raise DiagnosticBackendError("Loki returned malformed log data")
                event = _event(str(value[0]), str(value[1]))
                if event is not None:
                    events.append(event)
        return sorted(events, key=lambda event: event.timestamp)[:limit]

    def search(self, request: LogSearchInput) -> list[LogEvent]:
        """Run one controlled structured log search."""

        return self._query(build_log_query(request), request.window, request.limit)

    def correlation(self, correlation_id: CorrelationId, limit: int = 100) -> list[LogEvent]:
        """Return chronological events for one exact correlation ID."""

        return self._query(
            build_correlation_query(correlation_id),
            DiagnosticWindow.THIRTY_MINUTES,
            min(limit, 100),
        )

    def recent_errors(
        self,
        service: ApplicationService,
        window: DiagnosticWindow,
        limit: int,
    ) -> list[LogEvent]:
        """Return bounded structured 5xx events."""

        return self._query(build_recent_errors_query(service), window, min(limit, 100))

    def close(self) -> None:
        """Close the Loki connection pool."""

        self._client.close()
