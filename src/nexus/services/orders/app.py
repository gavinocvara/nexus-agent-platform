"""PostgreSQL-backed Orders service."""

from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from nexus.config import OrdersSettings
from nexus.contracts import CreateOrderRequest, HealthResponse, HealthStatus, OrderResponse
from nexus.services.orders.database import OrderRepository, OrdersDatabase
from nexus.web import ServiceError, install_service_foundation


def _get_session(request: Request) -> Generator[Session]:
    database: OrdersDatabase = request.app.state.database
    yield from database.sessions()


SessionDependency = Annotated[Session, Depends(_get_session)]


def create_app(
    settings: OrdersSettings | None = None,
    database: OrdersDatabase | None = None,
) -> FastAPI:
    """Create a configured Orders service application."""

    resolved_settings = settings or OrdersSettings()
    resolved_database = database or OrdersDatabase(resolved_settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_database.ping()
        app.state.database = resolved_database
        yield
        resolved_database.dispose()

    app = FastAPI(title="NEXUS Orders Service", version="0.2.0", lifespan=lifespan)
    install_service_foundation(
        app,
        resolved_settings.service_name,
        resolved_settings.log_level,
    )

    @app.get(
        "/health",
        response_model=HealthResponse,
        responses={503: {"model": HealthResponse}},
    )
    def health() -> HealthResponse | JSONResponse:
        try:
            resolved_database.ping()
        except SQLAlchemyError:
            body = HealthResponse(
                service="orders",
                status=HealthStatus.UNHEALTHY,
                dependencies={"database": HealthStatus.UNHEALTHY},
            )
            return JSONResponse(status_code=503, content=body.model_dump(mode="json"))
        return HealthResponse(
            service="orders",
            status=HealthStatus.HEALTHY,
            dependencies={"database": HealthStatus.HEALTHY},
        )

    @app.post("/orders", response_model=OrderResponse, status_code=201)
    def create_order(payload: CreateOrderRequest, session: SessionDependency) -> OrderResponse:
        try:
            record = OrderRepository(session).create(payload)
        except SQLAlchemyError as exc:
            session.rollback()
            raise ServiceError(503, "database_unavailable", "Order could not be stored") from exc
        return OrderResponse.model_validate(record)

    @app.get("/orders/{order_id}", response_model=OrderResponse)
    def get_order(order_id: UUID, session: SessionDependency) -> OrderResponse:
        try:
            record = OrderRepository(session).get(order_id)
        except SQLAlchemyError as exc:
            raise ServiceError(503, "database_unavailable", "Order could not be read") from exc
        if record is None:
            raise ServiceError(404, "order_not_found", f"Order {order_id} was not found")
        return OrderResponse.model_validate(record)

    return app
