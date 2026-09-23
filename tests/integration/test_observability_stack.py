import json
import os
from collections.abc import Callable
from time import perf_counter, sleep, time
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration

GATEWAY = "http://localhost:8000"
USERS = "http://localhost:8001"
ORDERS = "http://localhost:8002"
PROMETHEUS = "http://localhost:9090"
LOKI = "http://localhost:3100"
TEMPO = "http://localhost:3200"
GRAFANA = "http://localhost:3000"
PROHIBITED_GROUND_TRUTH = (
    "users_unavailable",
    "users_latency",
    "orders_unavailable",
    "orders_latency",
    "orders_database_unavailable",
    "expected_root_cause",
    "expected_unaffected_components",
    "activation instructions",
    "difficulty",
    "failure_id",
)


@pytest.fixture(scope="module", autouse=True)
def require_compose() -> None:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 with the Compose environment running")


def _poll(probe: Callable[[], Any], predicate: Callable[[Any], bool], timeout: float = 30) -> Any:
    deadline = perf_counter() + timeout
    last_value: Any = None
    while perf_counter() < deadline:
        try:
            last_value = probe()
            if predicate(last_value):
                return last_value
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            pass
        sleep(0.5)
    pytest.fail(f"Telemetry condition was not met; last value: {last_value!r}")


def _prometheus_value(query: str) -> float | None:
    response = httpx.get(
        f"{PROMETHEUS}/api/v1/query",
        params={"query": query},
        timeout=5,
    )
    response.raise_for_status()
    results = response.json()["data"]["result"]
    return float(results[0]["value"][1]) if results else None


def _loki_lines(correlation_id: str) -> list[str]:
    response = httpx.get(
        f"{LOKI}/loki/api/v1/query_range",
        params={
            "query": f'{{service=~"gateway|users|orders"}} |= "{correlation_id}"',
            "since": "5m",
            "limit": "100",
        },
        timeout=5,
    )
    response.raise_for_status()
    results = response.json()["data"]["result"]
    return [line for stream in results for _timestamp, line in stream["values"]]


def _activate(service_url: str, failure_id: str) -> None:
    response = httpx.post(
        f"{service_url}/__lab/failures/activate",
        json={"failure_id": failure_id},
        timeout=5,
    )
    assert response.status_code == 200


def _reset(service_url: str) -> None:
    response = httpx.post(f"{service_url}/__lab/failures/reset-all", timeout=5)
    assert response.status_code == 200
    assert response.json()["active"] is False


def _assert_ground_truth_absent(value: str) -> None:
    assert all(term not in value for term in PROHIBITED_GROUND_TRUTH)


def test_request_is_correlated_across_metrics_logs_and_trace() -> None:
    correlation_id = f"observability-{int(time())}"
    created = httpx.post(
        f"{GATEWAY}/orders",
        headers={"X-Correlation-ID": correlation_id},
        json={"user_id": 1, "item": "telemetry-probe", "quantity": 1},
        timeout=10,
    )
    assert created.status_code == 201
    assert created.headers["X-Correlation-ID"] == correlation_id
    _assert_ground_truth_absent(created.text)

    metric = _poll(
        lambda: _prometheus_value(
            'nexus_http_requests_total{service="gateway",route="/orders",status_class="2xx"}'
        ),
        lambda value: value is not None and value >= 1,
    )
    assert metric >= 1

    log_lines = _poll(
        lambda: _loki_lines(correlation_id),
        lambda lines: len(lines) >= 2,
    )
    parsed_logs = [json.loads(line) for line in log_lines if line.startswith("{")]
    trace_ids = {entry["trace_id"] for entry in parsed_logs if "trace_id" in entry}
    assert len(trace_ids) == 1
    trace_id = trace_ids.pop()
    assert {entry["service"] for entry in parsed_logs} >= {"gateway", "orders"}

    trace_response = _poll(
        lambda: httpx.get(f"{TEMPO}/api/traces/{trace_id}", timeout=5),
        lambda response: (
            response.status_code == 200 and "gateway" in response.text and "orders" in response.text
        ),
    )
    trace_payload = trace_response.json()
    serialized_trace = json.dumps(trace_payload)
    assert '"service.name"' in serialized_trace
    assert "gateway" in serialized_trace
    assert "orders" in serialized_trace
    assert "db.system" in serialized_trace or "postgresql" in serialized_trace


def test_users_unavailable_produces_operational_error_evidence() -> None:
    correlation_id = "obs-users-unavailable"
    _reset(USERS)
    try:
        _activate(USERS, "users_unavailable")
        response = httpx.get(
            f"{GATEWAY}/users/1",
            headers={"X-Correlation-ID": correlation_id},
            timeout=10,
        )
        assert response.status_code == 503
        _assert_ground_truth_absent(response.text)
        failure_count = _poll(
            lambda: _prometheus_value(
                'nexus_dependency_failures_total{service="gateway",dependency="users",status_class="5xx"}'
            ),
            lambda value: value is not None and value >= 1,
        )
        assert failure_count >= 1
        logs = _poll(lambda: _loki_lines(correlation_id), lambda lines: len(lines) >= 2)
        assert any('"status_code":503' in line for line in logs)
    finally:
        _reset(USERS)
    assert httpx.get(f"{USERS}/health", timeout=5).status_code == 200


def test_users_latency_produces_slow_success_evidence() -> None:
    correlation_id = "obs-users-latency"
    _reset(USERS)
    try:
        _activate(USERS, "users_latency")
        started = perf_counter()
        response = httpx.get(
            f"{GATEWAY}/users/1",
            headers={"X-Correlation-ID": correlation_id},
            timeout=10,
        )
        elapsed = perf_counter() - started
        assert response.status_code == 200
        assert elapsed >= 1.3
        _assert_ground_truth_absent(response.text)
        duration_sum = _poll(
            lambda: _prometheus_value(
                'nexus_dependency_request_duration_seconds_sum{service="gateway",dependency="users",status_class="2xx"}'
            ),
            lambda value: value is not None and value >= 1.3,
        )
        assert duration_sum >= 1.3
        logs = _poll(lambda: _loki_lines(correlation_id), lambda lines: len(lines) >= 2)
        durations = [
            json.loads(line).get("duration_ms", 0) for line in logs if line.startswith("{")
        ]
        assert max(durations) >= 1300
    finally:
        _reset(USERS)
    assert httpx.get(f"{USERS}/health", timeout=5).status_code == 200


def test_database_failure_updates_health_metrics_and_recovers() -> None:
    correlation_id = "obs-database-unavailable"
    _reset(ORDERS)
    try:
        _activate(ORDERS, "orders_database_unavailable")
        assert httpx.get(f"{ORDERS}/health", timeout=5).status_code == 503
        response = httpx.post(
            f"{GATEWAY}/orders",
            headers={"X-Correlation-ID": correlation_id},
            json={"user_id": 1, "item": "telemetry-probe", "quantity": 1},
            timeout=10,
        )
        assert response.status_code == 503
        _assert_ground_truth_absent(response.text)
        database_health = _poll(
            lambda: _prometheus_value(
                'nexus_database_health{service="orders",dependency="postgres"}'
            ),
            lambda value: value == 0,
        )
        assert database_health == 0
        logs = _poll(lambda: _loki_lines(correlation_id), lambda lines: len(lines) >= 2)
        assert any('"status_code":503' in line for line in logs)
    finally:
        _reset(ORDERS)
    assert httpx.get(f"{ORDERS}/health", timeout=5).status_code == 200
    recovered = _poll(
        lambda: _prometheus_value('nexus_database_health{service="orders",dependency="postgres"}'),
        lambda value: value == 1,
    )
    assert recovered == 1


def test_telemetry_contains_no_evaluator_ground_truth() -> None:
    metrics = "\n".join(
        httpx.get(f"http://localhost:{port}/metrics", timeout=5).text for port in (8000, 8001, 8002)
    )
    _assert_ground_truth_absent(metrics)

    for term in PROHIBITED_GROUND_TRUTH:
        response = httpx.get(
            f"{LOKI}/loki/api/v1/query_range",
            params={"query": f'{{service=~"gateway|users|orders"}} |= "{term}"', "since": "10m"},
            timeout=5,
        )
        response.raise_for_status()
        assert response.json()["data"]["result"] == []

    search = httpx.get(f"{TEMPO}/api/search", params={"limit": "20"}, timeout=5)
    search.raise_for_status()
    for trace_summary in search.json().get("traces", []):
        trace_id = trace_summary["traceID"]
        trace_response = httpx.get(f"{TEMPO}/api/traces/{trace_id}", timeout=5)
        trace_response.raise_for_status()
        _assert_ground_truth_absent(trace_response.text)


def test_grafana_provisions_data_sources_and_overview_dashboard() -> None:
    data_sources = httpx.get(f"{GRAFANA}/api/datasources", timeout=5)
    data_sources.raise_for_status()
    assert {item["name"] for item in data_sources.json()} >= {"Prometheus", "Loki", "Tempo"}

    dashboard = httpx.get(f"{GRAFANA}/api/dashboards/uid/aegisops-overview", timeout=5)
    dashboard.raise_for_status()
    assert dashboard.json()["dashboard"]["title"] == "AegisOps Overview"
