"""External HTTP gateway for the AegisOps lab."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI, Request

from nexus.config import GatewaySettings
from nexus.contracts import (
    CreateOrderRequest,
    HealthResponse,
    HealthStatus,
    OrderResponse,
    UserResponse,
)
from nexus.web import (
    CORRELATION_HEADER,
    ServiceError,
    get_correlation_id,
    install_service_foundation,
)


async def _request_downstream(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    json_body: dict[str, Any] | None = None,
) -> Any:
    try:
        response = await client.request(
            method,
            url,
            json=json_body,
            headers={CORRELATION_HEADER: get_correlation_id()},
        )
    except httpx.RequestError as exc:
        raise ServiceError(
            503,
            "downstream_unavailable",
            "A required downstream service is unavailable",
        ) from exc

    if response.is_error:
        try:
            detail = response.json()["error"]
            code = str(detail["code"])
            message = str(detail["message"])
        except (KeyError, TypeError, ValueError):
            code = "downstream_error"
            message = "A downstream service returned an error"
        raise ServiceError(response.status_code, code, message)

    try:
        return response.json()
    except ValueError as exc:
        raise ServiceError(
            502, "invalid_downstream_response", "Invalid downstream response"
        ) from exc


def create_app(
    settings: GatewaySettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Create a configured Gateway application."""

    resolved_settings = settings or GatewaySettings()
    users_url = str(resolved_settings.users_service_url).rstrip("/")
    orders_url = str(resolved_settings.orders_service_url).rstrip("/")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            timeout=resolved_settings.request_timeout_seconds,
            transport=transport,
        ) as client:
            app.state.http_client = client
            yield

    app = FastAPI(title="NEXUS API Gateway", version="0.3.0", lifespan=lifespan)
    install_service_foundation(
        app,
        resolved_settings.service_name,
        resolved_settings.log_level,
    )

    @app.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
    def health() -> HealthResponse:
        return HealthResponse(service="gateway", status=HealthStatus.HEALTHY)

    @app.get("/users/{user_id}", response_model=UserResponse)
    async def get_user(user_id: int, request: Request) -> UserResponse:
        payload = await _request_downstream(
            request.app.state.http_client,
            "GET",
            f"{users_url}/users/{user_id}",
        )
        return UserResponse.model_validate(payload)

    @app.post("/orders", response_model=OrderResponse, status_code=201)
    async def create_order(payload: CreateOrderRequest, request: Request) -> OrderResponse:
        response_payload = await _request_downstream(
            request.app.state.http_client,
            "POST",
            f"{orders_url}/orders",
            payload.model_dump(mode="json"),
        )
        return OrderResponse.model_validate(response_payload)

    @app.get("/orders/{order_id}", response_model=OrderResponse)
    async def get_order(order_id: UUID, request: Request) -> OrderResponse:
        payload = await _request_downstream(
            request.app.state.http_client,
            "GET",
            f"{orders_url}/orders/{order_id}",
        )
        return OrderResponse.model_validate(payload)

    return app
