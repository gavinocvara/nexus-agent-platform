"""HTTP contracts shared by the AegisOps lab services."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HealthStatus(StrEnum):
    """Supported service health states."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class HealthResponse(BaseModel):
    """Consistent service health response."""

    service: str
    status: HealthStatus
    dependencies: dict[str, HealthStatus] | None = None


class UserResponse(BaseModel):
    """Public deterministic user representation."""

    user_id: int
    name: str
    email: str


class CreateOrderRequest(BaseModel):
    """Input required to create an order."""

    user_id: int = Field(gt=0)
    item: str = Field(min_length=1, max_length=200)
    quantity: int = Field(gt=0, le=1000)


class OrderResponse(BaseModel):
    """Persisted order representation."""

    model_config = ConfigDict(from_attributes=True)

    order_id: UUID
    user_id: int
    item: str
    quantity: int
    created_at: datetime


class ErrorDetail(BaseModel):
    """Stable error details returned by every service."""

    code: str
    message: str
    correlation_id: str


class ErrorResponse(BaseModel):
    """Service error envelope."""

    error: ErrorDetail
