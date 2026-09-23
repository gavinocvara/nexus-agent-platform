"""Fixed-route service health adapter."""

from typing import Any

import httpx

from nexus.diagnostics._http import BoundedJsonClient, DiagnosticBackendError
from nexus.diagnostics.config import DiagnosticsSettings
from nexus.diagnostics.models import DiagnosticService, HealthState, ServiceHealthSnapshot


def _health_state(value: Any) -> HealthState:
    try:
        return HealthState(str(value))
    except ValueError:
        return HealthState.UNKNOWN


class HealthAdapter:
    """Read only predefined `/health` endpoints for registered services."""

    def __init__(
        self,
        settings: DiagnosticsSettings,
        transports: dict[DiagnosticService, httpx.BaseTransport] | None = None,
    ) -> None:
        configured = {
            DiagnosticService.GATEWAY: str(settings.gateway_url),
            DiagnosticService.USERS: str(settings.users_url),
            DiagnosticService.ORDERS: str(settings.orders_url),
        }
        self._clients = {
            service: BoundedJsonClient(
                url,
                settings.timeout_seconds,
                settings.max_response_bytes,
                (transports or {}).get(service),
            )
            for service, url in configured.items()
        }

    def get(self, service: DiagnosticService) -> ServiceHealthSnapshot:
        """Return normalized health without accepting a URL or path."""

        queried_service = (
            DiagnosticService.ORDERS if service is DiagnosticService.POSTGRES else service
        )
        try:
            payload = self._clients[queried_service].get_json(
                "/health", accepted_statuses=frozenset({200, 503})
            )
        except DiagnosticBackendError:
            return ServiceHealthSnapshot(service=service, status=HealthState.UNAVAILABLE)

        raw_dependencies = payload.get("dependencies", {})
        dependencies: dict[DiagnosticService, HealthState] = {}
        if isinstance(raw_dependencies, dict) and "database" in raw_dependencies:
            dependencies[DiagnosticService.POSTGRES] = _health_state(raw_dependencies["database"])

        if service is DiagnosticService.POSTGRES:
            return ServiceHealthSnapshot(
                service=service,
                status=dependencies.get(DiagnosticService.POSTGRES, HealthState.UNKNOWN),
            )
        return ServiceHealthSnapshot(
            service=service,
            status=_health_state(payload.get("status")),
            dependencies=dependencies,
        )

    def close(self) -> None:
        """Close all fixed service clients."""

        for client in self._clients.values():
            client.close()
