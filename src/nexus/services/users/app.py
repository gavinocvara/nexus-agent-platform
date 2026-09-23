"""Deterministic Users service for the AegisOps lab."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from nexus.config import UsersSettings
from nexus.contracts import HealthResponse, HealthStatus, UserResponse
from nexus.lab.control import create_failure_router
from nexus.lab.failures import FailureController, FailureDefinition, FailureEffect
from nexus.observability import install_observability
from nexus.web import ServiceError, install_service_foundation

_USERS = {
    1: UserResponse(user_id=1, name="Ada Lovelace", email="ada@example.test"),
    2: UserResponse(user_id=2, name="Grace Hopper", email="grace@example.test"),
}

USER_FAILURES = (
    FailureDefinition("users_unavailable", FailureEffect.SERVICE_UNAVAILABLE),
    FailureDefinition("users_latency", FailureEffect.LATENCY, delay_ms=1500),
)


def create_app(
    settings: UsersSettings | None = None,
    failure_controller: FailureController | None = None,
) -> FastAPI:
    """Create a configured Users service application."""

    resolved_settings = settings or UsersSettings()
    controller = failure_controller or FailureController(USER_FAILURES)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            runtime.shutdown()

    app = FastAPI(title="NEXUS Users Service", version="0.5.0", lifespan=lifespan)
    install_service_foundation(
        app,
        resolved_settings.service_name,
        resolved_settings.log_level,
    )
    runtime = install_observability(app, resolved_settings, resolved_settings.service_name)

    if resolved_settings.lab_failures_enabled:
        app.include_router(create_failure_router(controller))

    @app.get(
        "/health",
        response_model=HealthResponse,
        response_model_exclude_none=True,
        responses={503: {"model": HealthResponse}},
    )
    def health() -> HealthResponse | JSONResponse:
        if controller.is_active(FailureEffect.SERVICE_UNAVAILABLE):
            body = HealthResponse(service="users", status=HealthStatus.UNHEALTHY)
            return JSONResponse(status_code=503, content=body.model_dump(mode="json"))
        return HealthResponse(service="users", status=HealthStatus.HEALTHY)

    @app.get("/users/{user_id}", response_model=UserResponse)
    def get_user(user_id: int) -> UserResponse:
        if controller.is_active(FailureEffect.SERVICE_UNAVAILABLE):
            raise ServiceError(503, "service_unavailable", "Users service is unavailable")
        controller.apply_latency()
        user = _USERS.get(user_id)
        if user is None:
            raise ServiceError(404, "user_not_found", f"User {user_id} was not found")
        return user

    return app
