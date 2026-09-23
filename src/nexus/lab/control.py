"""Privileged service-local HTTP control surface for lab failures."""

from fastapi import APIRouter
from pydantic import BaseModel

from nexus.lab.failures import FailureController, FailureDefinition, UnknownFailureError
from nexus.web import ServiceError

LAB_PREFIX = "/__lab/failures"


class ActivateFailureRequest(BaseModel):
    """Validated activation input."""

    failure_id: str


class FailureDefinitionResponse(BaseModel):
    """Control-plane view of one supported operational fault."""

    failure_id: str
    effect: str
    delay_ms: int | None = None


class FailureStateResponse(BaseModel):
    """Control-plane activation state."""

    active: bool
    failure: FailureDefinitionResponse | None = None


class FailureCatalogResponse(BaseModel):
    """Failures supported by one service process."""

    failures: list[FailureDefinitionResponse]


def _definition_response(definition: FailureDefinition) -> FailureDefinitionResponse:
    return FailureDefinitionResponse(
        failure_id=definition.failure_id,
        effect=definition.effect.value,
        delay_ms=definition.delay_ms,
    )


def _state_response(controller: FailureController) -> FailureStateResponse:
    active = controller.state().active_failure
    return FailureStateResponse(
        active=active is not None,
        failure=_definition_response(active) if active is not None else None,
    )


def create_failure_router(controller: FailureController) -> APIRouter:
    """Create an enabled lab-only router for one service controller."""

    router = APIRouter(prefix=LAB_PREFIX, tags=["lab-control"])

    @router.get("", response_model=FailureCatalogResponse)
    def list_failures() -> FailureCatalogResponse:
        return FailureCatalogResponse(
            failures=[_definition_response(item) for item in controller.list_definitions()]
        )

    @router.get("/state", response_model=FailureStateResponse)
    def get_state() -> FailureStateResponse:
        return _state_response(controller)

    @router.post("/activate", response_model=FailureStateResponse)
    def activate(request: ActivateFailureRequest) -> FailureStateResponse:
        try:
            controller.activate(request.failure_id)
        except UnknownFailureError as exc:
            raise ServiceError(404, "unknown_lab_failure", "Unknown lab failure") from exc
        return _state_response(controller)

    @router.post("/reset", response_model=FailureStateResponse)
    def reset() -> FailureStateResponse:
        controller.reset()
        return _state_response(controller)

    @router.post("/reset-all", response_model=FailureStateResponse)
    def reset_all() -> FailureStateResponse:
        controller.reset()
        return _state_response(controller)

    return router
