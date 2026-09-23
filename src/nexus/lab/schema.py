"""Versioned evaluator-only ground-truth scenario schema."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, model_validator


class ScenarioService(StrEnum):
    """Services addressable by the Phase 2 evaluator."""

    GATEWAY = "gateway"
    USERS = "users"
    ORDERS = "orders"


class FailureClass(StrEnum):
    """Ground-truth failure classes."""

    SERVICE_UNAVAILABLE = "service_unavailable"
    LATENCY = "latency"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"


class ScenarioTarget(BaseModel):
    """Component intentionally affected by a scenario."""

    service: ScenarioService
    dependency: str | None = None


class ScenarioFault(BaseModel):
    """Deterministic fault configuration."""

    type: FailureClass
    delay_ms: int | None = Field(default=None, ge=50, le=10_000)

    @model_validator(mode="after")
    def validate_delay(self) -> "ScenarioFault":
        if self.type is FailureClass.LATENCY and self.delay_ms is None:
            raise ValueError("Latency scenarios require delay_ms")
        if self.type is not FailureClass.LATENCY and self.delay_ms is not None:
            raise ValueError("Only latency scenarios may define delay_ms")
        return self


class ExpectedRootCause(BaseModel):
    """Evaluator-only root cause used by future scoring."""

    component: str
    failure: str


class ExpectedObservation(BaseModel):
    """One machine-verifiable symptom visible through an ordinary API."""

    description: str
    via: ScenarioService
    method: Literal["GET", "POST"]
    path: str = Field(pattern=r"^/")
    expected_status: int = Field(ge=100, le=599)
    minimum_duration_ms: int | None = Field(default=None, ge=1, le=10_000)
    request_body: dict[str, JsonValue] | None = None


class ActivationInstruction(BaseModel):
    """Evaluator control-plane routing for a scenario."""

    controller_service: Literal[ScenarioService.USERS, ScenarioService.ORDERS]
    failure_id: str


class RecoveryExpectation(BaseModel):
    """Required reset and recovery behavior."""

    reset_required: Literal[True] = True
    health_service: Literal[ScenarioService.USERS, ScenarioService.ORDERS]
    expected_status: Literal[200] = 200


class FailureScenario(BaseModel):
    """Version 1 deterministic incident scenario with evaluator ground truth."""

    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str
    description: str
    target: ScenarioTarget
    fault: ScenarioFault
    expected_root_cause: ExpectedRootCause
    expected_symptoms: list[ExpectedObservation] = Field(min_length=1)
    expected_unaffected_components: list[str] = Field(min_length=1)
    activation: ActivationInstruction
    recovery: RecoveryExpectation
    tags: list[str] = Field(min_length=1)
    difficulty: Literal["basic", "intermediate"]

    @model_validator(mode="after")
    def validate_target_alignment(self) -> "FailureScenario":
        if self.target.service.value != self.activation.controller_service.value:
            raise ValueError("Scenario target and controller service must match")
        if self.id != self.activation.failure_id:
            raise ValueError("Scenario ID and service failure ID must match")
        return self
