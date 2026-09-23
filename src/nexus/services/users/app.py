"""Deterministic Users service for the AegisOps lab."""

from fastapi import FastAPI

from nexus.config import UsersSettings
from nexus.contracts import HealthResponse, HealthStatus, UserResponse
from nexus.web import ServiceError, install_service_foundation

_USERS = {
    1: UserResponse(user_id=1, name="Ada Lovelace", email="ada@example.test"),
    2: UserResponse(user_id=2, name="Grace Hopper", email="grace@example.test"),
}


def create_app(settings: UsersSettings | None = None) -> FastAPI:
    """Create a configured Users service application."""

    resolved_settings = settings or UsersSettings()
    app = FastAPI(title="NEXUS Users Service", version="0.2.0")
    install_service_foundation(
        app,
        resolved_settings.service_name,
        resolved_settings.log_level,
    )

    @app.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
    def health() -> HealthResponse:
        return HealthResponse(service="users", status=HealthStatus.HEALTHY)

    @app.get("/users/{user_id}", response_model=UserResponse)
    def get_user(user_id: int) -> UserResponse:
        user = _USERS.get(user_id)
        if user is None:
            raise ServiceError(404, "user_not_found", f"User {user_id} was not found")
        return user

    return app
