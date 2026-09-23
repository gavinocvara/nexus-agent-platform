import os
from collections.abc import Callable
from time import perf_counter, sleep, time
from typing import Any

import httpx
import pytest

from nexus.diagnostics.models import (
    CorrelationInput,
    DatabaseHealthInput,
    DependencySummaryInput,
    DiagnosticService,
    HealthState,
    RecentErrorsInput,
    RequestSummaryInput,
    ServiceInput,
    TraceInput,
)
from nexus.diagnostics.service import DiagnosticServiceLayer
from nexus.lab.catalog import ScenarioCatalog

pytestmark = pytest.mark.integration

GATEWAY = "http://localhost:8000"
USERS = "http://localhost:8001"
ORDERS = "http://localhost:8002"
SERVICE_URLS = {"users": USERS, "orders": ORDERS}


@pytest.fixture(scope="module", autouse=True)
def require_compose() -> None:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 with the Compose environment running")


def _poll(probe: Callable[[], Any], predicate: Callable[[Any], bool], timeout: float = 30) -> Any:
    deadline = perf_counter() + timeout
    last_value: Any = None
    while perf_counter() < deadline:
        last_value = probe()
        if predicate(last_value):
            return last_value
        sleep(0.5)
    pytest.fail(f"Diagnostic condition was not met; last value: {last_value!r}")


def _activate(service: str, failure_id: str) -> None:
    response = httpx.post(
        f"{SERVICE_URLS[service]}/__lab/failures/activate",
        json={"failure_id": failure_id},
        timeout=5,
    )
    assert response.status_code == 200


def _reset(service: str) -> None:
    response = httpx.post(f"{SERVICE_URLS[service]}/__lab/failures/reset-all", timeout=5)
    assert response.status_code == 200


def _exercise(scenario_id: str, correlation_id: str) -> httpx.Response:
    if scenario_id.startswith("users_"):
        return httpx.get(
            f"{GATEWAY}/users/1",
            headers={"X-Correlation-ID": correlation_id},
            timeout=10,
        )
    return httpx.post(
        f"{GATEWAY}/orders",
        headers={"X-Correlation-ID": correlation_id},
        json={"user_id": 1, "item": "diagnostic-probe", "quantity": 1},
        timeout=10,
    )


def _trace_for(diagnostics: DiagnosticServiceLayer, correlation_id: str) -> Any:
    found = _poll(
        lambda: diagnostics.find_traces(CorrelationInput(correlation_id=correlation_id)),
        lambda result: result.success and bool(result.trace_ids),
    )
    return _poll(
        lambda: diagnostics.get_trace(TraceInput(trace_id=found.trace_ids[0])),
        lambda result: result.success and result.trace is not None,
    )


def test_healthy_diagnostic_workflow_against_real_backends() -> None:
    correlation_id = f"diagnostic-healthy-{int(time())}"
    response = httpx.get(
        f"{GATEWAY}/users/1",
        headers={"X-Correlation-ID": correlation_id},
        timeout=10,
    )
    assert response.status_code == 200

    with DiagnosticServiceLayer() as diagnostics:
        system = diagnostics.get_system_health()
        assert system.success
        assert {item.service for item in system.services} == set(DiagnosticService)
        assert all(item.status is HealthState.HEALTHY for item in system.services)

        requests = _poll(
            lambda: diagnostics.get_request_summary(
                RequestSummaryInput(service="gateway", window="5m")
            ),
            lambda result: (
                result.success
                and result.summary is not None
                and result.summary.request_count is not None
                and result.summary.request_count > 0
            ),
        )
        assert requests.summary.request_rate_per_second is not None

        dependency = _poll(
            lambda: diagnostics.get_dependency_summary(
                DependencySummaryInput(service="gateway", dependency="users", window="5m")
            ),
            lambda result: (
                result.success
                and result.summary is not None
                and result.summary.request_count is not None
                and result.summary.request_count > 0
            ),
        )
        assert dependency.summary.request_rate_per_second is not None

        evidence = _poll(
            lambda: diagnostics.get_request_evidence(
                CorrelationInput(correlation_id=correlation_id)
            ),
            lambda result: result.success and len(result.events) >= 2,
        )
        assert {event.service.value for event in evidence.events} >= {"gateway", "users"}
        trace = _trace_for(diagnostics, correlation_id)
        assert {span.service for span in trace.trace.spans} >= {"gateway", "users"}
        assert diagnostics.session.tool_call_count >= 6


def test_users_unavailable_evidence_uses_only_diagnostic_tools() -> None:
    correlation_id = "diagnostic-users-unavailable"
    _reset("users")
    try:
        _activate("users", "users_unavailable")
        assert _exercise("users_unavailable", correlation_id).status_code == 503
        with DiagnosticServiceLayer() as diagnostics:
            health = diagnostics.get_service_health(ServiceInput(service="users"))
            assert health.health is not None
            assert health.health.status is HealthState.UNHEALTHY
            dependency = _poll(
                lambda: diagnostics.get_dependency_summary(
                    DependencySummaryInput(service="gateway", dependency="users", window="5m")
                ),
                lambda result: (
                    result.summary is not None
                    and result.summary.failure_count is not None
                    and result.summary.failure_count > 0
                ),
            )
            assert dependency.success
            errors = _poll(
                lambda: diagnostics.get_recent_errors(
                    RecentErrorsInput(service="users", window="5m", limit=20)
                ),
                lambda result: any(event.status_code == 503 for event in result.events),
            )
            assert errors.content_is_untrusted
            trace = _trace_for(diagnostics, correlation_id)
            assert any("ERROR" in span.status for span in trace.trace.spans)
            orders = diagnostics.get_service_health(ServiceInput(service="orders"))
            postgres = diagnostics.get_service_health(ServiceInput(service="postgres"))
            assert orders.health is not None and orders.health.status is HealthState.HEALTHY
            assert postgres.health is not None and postgres.health.status is HealthState.HEALTHY
    finally:
        _reset("users")


def test_users_latency_evidence_uses_only_diagnostic_tools() -> None:
    correlation_id = "diagnostic-users-latency"
    _reset("users")
    try:
        _activate("users", "users_latency")
        started = perf_counter()
        response = _exercise("users_latency", correlation_id)
        elapsed_ms = (perf_counter() - started) * 1000
        assert response.status_code == 200
        assert elapsed_ms >= 1300
        with DiagnosticServiceLayer() as diagnostics:
            health = diagnostics.get_service_health(ServiceInput(service="users"))
            assert health.health is not None and health.health.status is HealthState.HEALTHY
            dependency = _poll(
                lambda: diagnostics.get_dependency_summary(
                    DependencySummaryInput(service="gateway", dependency="users", window="5m")
                ),
                lambda result: (
                    result.summary is not None
                    and result.summary.latency_p95_ms is not None
                    and result.summary.latency_p95_ms >= 1000
                ),
            )
            assert dependency.success
            evidence = _poll(
                lambda: diagnostics.get_request_evidence(
                    CorrelationInput(correlation_id=correlation_id)
                ),
                lambda result: any((event.duration_ms or 0) >= 1300 for event in result.events),
            )
            assert any(event.status_code == 200 for event in evidence.events)
            trace = _trace_for(diagnostics, correlation_id)
            assert max(span.duration_ms for span in trace.trace.spans) >= 1300
    finally:
        _reset("users")


def test_orders_database_unavailable_evidence_uses_only_diagnostic_tools() -> None:
    correlation_id = "diagnostic-orders-database"
    _reset("orders")
    try:
        _activate("orders", "orders_database_unavailable")
        assert httpx.get(f"{ORDERS}/health", timeout=5).status_code == 503
        assert _exercise("orders_database_unavailable", correlation_id).status_code == 503
        with DiagnosticServiceLayer() as diagnostics:
            orders = diagnostics.get_service_health(ServiceInput(service="orders"))
            assert orders.health is not None
            assert orders.health.status is HealthState.UNHEALTHY
            assert orders.health.dependencies[DiagnosticService.POSTGRES] is HealthState.UNHEALTHY
            database = _poll(
                lambda: diagnostics.get_database_health(DatabaseHealthInput()),
                lambda result: result.success and result.healthy is False,
            )
            assert database.healthy is False
            errors = _poll(
                lambda: diagnostics.get_recent_errors(
                    RecentErrorsInput(service="orders", window="5m", limit=20)
                ),
                lambda result: any(event.status_code == 503 for event in result.events),
            )
            assert errors.events
            trace = _trace_for(diagnostics, correlation_id)
            assert any(
                span.attributes.get("error.type") == "database_unavailable"
                for span in trace.trace.spans
            )
            users = diagnostics.get_service_health(ServiceInput(service="users"))
            assert users.health is not None and users.health.status is HealthState.HEALTHY
    finally:
        _reset("orders")


def test_evaluator_can_distinguish_all_scenarios_from_permitted_evidence() -> None:
    catalog = ScenarioCatalog.load()
    fingerprints: dict[str, tuple[str, str, int, bool]] = {}
    serialized_diagnostics: list[str] = []

    for index, scenario in enumerate(catalog.list(), start=1):
        service = scenario.target.service.value
        correlation_id = f"diagnostic-sufficiency-{index}"
        _reset(service)
        try:
            _activate(service, scenario.id)
            response = _exercise(scenario.id, correlation_id)
            with DiagnosticServiceLayer() as diagnostics:
                health = diagnostics.get_service_health(
                    ServiceInput(service=DiagnosticService(service))
                )
                assert health.health is not None
                evidence = _poll(
                    lambda correlation_id=correlation_id: diagnostics.get_request_evidence(
                        CorrelationInput(correlation_id=correlation_id)
                    ),
                    lambda result: len(result.events) >= 2,
                )
                gateway_events = [
                    event for event in evidence.events if event.service.value == "gateway"
                ]
                assert gateway_events
                event = gateway_events[-1]
                database_state = health.health.dependencies.get(
                    DiagnosticService.POSTGRES, HealthState.UNKNOWN
                )
                fingerprints[scenario.id] = (
                    health.health.status.value,
                    database_state.value,
                    event.status_code or response.status_code,
                    (event.duration_ms or 0) >= 1300,
                )
                serialized_diagnostics.extend(
                    [
                        health.model_dump_json(),
                        evidence.model_dump_json(),
                        *(
                            record.model_dump_json()
                            for record in diagnostics.session.audit_records()
                        ),
                    ]
                )
        finally:
            _reset(service)

    assert len(fingerprints) == 5
    assert len(set(fingerprints.values())) == 5

    diagnostic_output = "\n".join(serialized_diagnostics)
    prohibited_fields = (
        "scenario_id",
        "scenario title",
        "expected_root_cause",
        "difficulty",
        "activation",
        "expected_unaffected_components",
        "failure-control",
        "lab/scenarios",
    )
    assert all(term not in diagnostic_output for term in prohibited_fields)
    assert all(scenario.id not in diagnostic_output for scenario in catalog.list())

    with DiagnosticServiceLayer() as diagnostics:
        recovered = diagnostics.get_system_health()
    assert all(item.status is HealthState.HEALTHY for item in recovered.services)
