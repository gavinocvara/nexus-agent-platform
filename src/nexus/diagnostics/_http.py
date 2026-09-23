"""Bounded JSON transport shared only by diagnostic backend adapters."""

from typing import Any

import httpx


class DiagnosticBackendError(RuntimeError):
    """Safe backend failure suitable for conversion into a typed warning."""


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
        except httpx.HTTPError as exc:
            raise DiagnosticBackendError("Diagnostic backend unavailable") from exc
        if len(response.content) > self._max_response_bytes:
            raise DiagnosticBackendError("Diagnostic backend response exceeded size limit")
        try:
            payload = response.json()
        except ValueError as exc:
            raise DiagnosticBackendError("Diagnostic backend returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise DiagnosticBackendError("Diagnostic backend returned an invalid payload")
        return payload

    def close(self) -> None:
        """Release the owned connection pool."""

        self._client.close()
