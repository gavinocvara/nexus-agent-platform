"""Evaluator-facing deterministic scenario lifecycle runner."""

import os
from enum import StrEnum
from time import perf_counter

import httpx
from pydantic import BaseModel

from nexus.lab.catalog import ScenarioCatalog
from nexus.lab.schema import ExpectedObservation, FailureScenario, ScenarioService

CONTROL_PREFIX = "/__lab/failures"


class ScenarioLifecycle(StrEnum):
    """Explicit lifecycle states for a scenario run."""

    DEFINED = "defined"
    BASELINE_VERIFIED = "baseline_verified"
    ACTIVE = "active"
    SYMPTOMS_VERIFIED = "symptoms_verified"
    RESET = "reset"
    RECOVERY_VERIFIED = "recovery_verified"


class ObservationResult(BaseModel):
    """Measured result for one expected symptom."""

    description: str
    status_code: int
    duration_ms: int


class ScenarioRunResult(BaseModel):
    """Evaluator-only result retaining the injected scenario identity."""

    scenario_id: str
    lifecycle: ScenarioLifecycle
    observations: list[ObservationResult]


class ScenarioRunError(RuntimeError):
    """Raised when activation, symptoms, reset, or recovery diverge from ground truth."""


class ScenarioRunner:
    """Drive one scenario through baseline, failure, reset, and recovery."""

    def __init__(
        self,
        catalog: ScenarioCatalog,
        service_urls: dict[ScenarioService, str],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.catalog = catalog
        self.service_urls = {service: url.rstrip("/") for service, url in service_urls.items()}
        self._transport = transport

    def _url(self, service: ScenarioService, path: str) -> str:
        try:
            base_url = self.service_urls[service]
        except KeyError as exc:
            raise ScenarioRunError(f"No URL configured for {service.value}") from exc
        return f"{base_url}{path}"

    def _control_url(self, scenario: FailureScenario, path: str) -> str:
        service = ScenarioService(scenario.activation.controller_service.value)
        return self._url(service, f"{CONTROL_PREFIX}{path}")

    def reset(self, scenario: FailureScenario, client: httpx.Client) -> None:
        """Idempotently reset a scenario's target service."""

        response = client.post(self._control_url(scenario, "/reset-all"))
        if response.status_code != 200 or response.json().get("active") is not False:
            raise ScenarioRunError("Failure reset did not produce clean state")

    def activate(self, scenario: FailureScenario, client: httpx.Client) -> None:
        """Activate and verify one scenario through its privileged control plane."""

        response = client.post(
            self._control_url(scenario, "/activate"),
            json={"failure_id": scenario.activation.failure_id},
        )
        body = response.json()
        failure = body.get("failure") or {}
        if response.status_code != 200 or failure.get("failure_id") != scenario.id:
            raise ScenarioRunError("Failure activation state did not match the scenario")

    def _verify_health(
        self,
        service: ScenarioService,
        expected_status: int,
        client: httpx.Client,
    ) -> None:
        response = client.get(self._url(service, "/health"))
        if response.status_code != expected_status:
            raise ScenarioRunError(
                f"{service.value} health returned {response.status_code}, "
                f"expected {expected_status}"
            )

    def _observe(
        self,
        expectation: ExpectedObservation,
        client: httpx.Client,
    ) -> ObservationResult:
        started = perf_counter()
        response = client.request(
            expectation.method,
            self._url(expectation.via, expectation.path),
            json=expectation.request_body,
            headers={"X-Correlation-ID": "scenario-runner"},
        )
        duration_ms = round((perf_counter() - started) * 1000)
        if response.status_code != expectation.expected_status:
            raise ScenarioRunError(
                f"{expectation.description} returned {response.status_code}, "
                f"expected {expectation.expected_status}"
            )
        if (
            expectation.minimum_duration_ms is not None
            and duration_ms < expectation.minimum_duration_ms
        ):
            raise ScenarioRunError(
                f"{expectation.description} took {duration_ms} ms, expected at least "
                f"{expectation.minimum_duration_ms} ms"
            )
        if response.headers.get("X-Correlation-ID") != "scenario-runner":
            raise ScenarioRunError("Correlation ID was not preserved")
        return ObservationResult(
            description=expectation.description,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

    def run(self, scenario_id: str) -> ScenarioRunResult:
        """Run a complete mandatory-recovery scenario lifecycle."""

        scenario = self.catalog.get(scenario_id)
        lifecycle = ScenarioLifecycle.DEFINED
        observations: list[ObservationResult] = []
        with httpx.Client(timeout=15, transport=self._transport) as client:
            self.reset(scenario, client)
            health_service = ScenarioService(scenario.recovery.health_service.value)
            self._verify_health(health_service, 200, client)
            lifecycle = ScenarioLifecycle.BASELINE_VERIFIED
            symptom_error: ScenarioRunError | None = None
            try:
                self.activate(scenario, client)
                lifecycle = ScenarioLifecycle.ACTIVE
                observations = [
                    self._observe(expectation, client) for expectation in scenario.expected_symptoms
                ]
                lifecycle = ScenarioLifecycle.SYMPTOMS_VERIFIED
            except ScenarioRunError as exc:
                symptom_error = exc
            finally:
                self.reset(scenario, client)
                lifecycle = ScenarioLifecycle.RESET
            self._verify_health(health_service, scenario.recovery.expected_status, client)
            lifecycle = ScenarioLifecycle.RECOVERY_VERIFIED
            if symptom_error is not None:
                raise symptom_error
        return ScenarioRunResult(
            scenario_id=scenario.id,
            lifecycle=lifecycle,
            observations=observations,
        )


def default_service_urls() -> dict[ScenarioService, str]:
    """Return host-side Compose URLs for the evaluator CLI."""

    return {
        ScenarioService.GATEWAY: os.getenv("NEXUS_LAB_GATEWAY_URL", "http://localhost:8000"),
        ScenarioService.USERS: os.getenv("NEXUS_LAB_USERS_URL", "http://localhost:8001"),
        ScenarioService.ORDERS: os.getenv("NEXUS_LAB_ORDERS_URL", "http://localhost:8002"),
    }
