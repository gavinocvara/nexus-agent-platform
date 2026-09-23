"""Bounded JSON transport shared only by diagnostic backend adapters."""

from typing import Any

import httpx

from nexus.diagnostics.models import DiagnosticBackendErrorCode


class DiagnosticBackendError(RuntimeError):
    """Safe backend failure suitable for conversion into a typed warning."""

    def __init__(self, code: DiagnosticBackendErrorCode, status_code: int | None = None) -> None:
        messages = {
            DiagnosticBackendErrorCode.TRANSPORT: "Diagnostic backend unavailable",
            DiagnosticBackendErrorCode.HTTP_STATUS: "Diagnostic backend returned an HTTP error",
            DiagnosticBackendErrorCode.RESPONSE_TOO_LARGE: (
                "Diagnostic backend response exceeded size limit"
            ),
            DiagnosticBackendErrorCode.MALFORMED_RESPONSE: (
                "Diagnostic backend returned invalid data"
            ),
            DiagnosticBackendErrorCode.QUERY_REJECTED: "Diagnostic backend rejected the query",
        }
        super().__init__(messages[code])
        self.code = code
        self.status_code = status_code


class BoundedJsonClient:
    """HTTP JSON client with fixed base URL, timeout, and response-size limits."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        max_response_bytes: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )
        self._max_response_bytes = max_response_bytes

    def get_json(
        self,
        path: str,
        params: dict[str, str] | None = None,
        accepted_statuses: frozenset[int] | None = None,
    ) -> dict[str, Any]:
        """GET one adapter-owned path and validate a bounded JSON object."""

        try:
            response = self._client.get(path, params=params)
            if accepted_statuses is None or response.status_code not in accepted_statuses:
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DiagnosticBackendError(
                DiagnosticBackendErrorCode.HTTP_STATUS, exc.response.status_code
            ) from exc
        except httpx.HTTPError as exc:
            raise DiagnosticBackendError(DiagnosticBackendErrorCode.TRANSPORT) from exc
        if len(response.content) > self._max_response_bytes:
            raise DiagnosticBackendError(DiagnosticBackendErrorCode.RESPONSE_TOO_LARGE)
        try:
            payload = response.json()
        except ValueError as exc:
            raise DiagnosticBackendError(DiagnosticBackendErrorCode.MALFORMED_RESPONSE) from exc
        if not isinstance(payload, dict):
            raise DiagnosticBackendError(DiagnosticBackendErrorCode.MALFORMED_RESPONSE)
        return payload

    def close(self) -> None:
        """Release the owned connection pool."""

        self._client.close()
