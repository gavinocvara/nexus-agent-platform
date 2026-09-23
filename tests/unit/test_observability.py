import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nexus.config import GatewaySettings, UsersSettings
from nexus.observability.metrics import ServiceMetrics
from nexus.observability.runtime import TelemetryMiddleware
from nexus.observability.tracing import create_tracing_runtime
from nexus.services.gateway.app import create_app as create_gateway_app
from nexus.services.users.app import create_app as create_users_app
from nexus.web import ServiceError, install_service_foundation


def test_metrics_use_route_templates_and_bounded_labels() -> None:
    app = create_users_app(UsersSettings(metrics_enabled=True))

    with TestClient(app) as client:
        assert client.get("/users/1").status_code == 200
        assert client.get("/users/999").status_code == 404
        metrics = client.get("/metrics").text

    assert "nexus_http_requests_total" in metrics
    assert "nexus_http_request_duration_seconds_count" in metrics
    assert 'route="/users/{user_id}"' in metrics
    assert 'status_class="2xx"' in metrics
    assert 'status_class="4xx"' in metrics
    assert 'route="/users/1"' not in metrics
    assert "correlation_id" not in metrics
    assert "trace_id" not in metrics
    assert 'user_id="' not in metrics


def test_gateway_dependency_metrics_are_bounded() -> None:
    def downstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"user_id": 1, "name": "Ada Lovelace", "email": "ada@example.test"},
        )

    app = create_gateway_app(
        GatewaySettings(metrics_enabled=True, users_service_url="http://users.test"),
        httpx.MockTransport(downstream),
    )
    with TestClient(app) as client:
        assert client.get("/users/1").status_code == 200
        metrics = client.get("/metrics").text

    assert "nexus_dependency_requests_total" in metrics
    assert 'dependency="users"' in metrics
    assert "users.test" not in metrics
    assert "/users/1" not in metrics


def test_server_spans_use_normalized_routes_without_identifiers() -> None:
    exporter = InMemorySpanExporter()
    runtime = create_tracing_runtime("test-service", True, "unused", exporter)
    app = FastAPI()
    app.add_middleware(
        TelemetryMiddleware,
        service="test-service",
        metrics=None,
        tracer=runtime.tracer,
    )

    @app.get("/items/{item_id}")
    def get_item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    with TestClient(app) as client:
        assert client.get("/items/high-cardinality-value").status_code == 200

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attributes = dict(spans[0].attributes or {})
    assert spans[0].name == "GET /items/{item_id}"
    assert attributes["http.route"] == "/items/{item_id}"
    assert "high-cardinality-value" not in str(attributes)
    runtime.shutdown()


def test_service_error_code_is_recorded_as_operational_trace_evidence() -> None:
    exporter = InMemorySpanExporter()
    runtime = create_tracing_runtime("test-service", True, "unused", exporter)
    app = FastAPI()
    install_service_foundation(app, "test-service", "INFO")
    app.add_middleware(
        TelemetryMiddleware,
        service="test-service",
        metrics=None,
        tracer=runtime.tracer,
    )

    @app.get("/failure")
    def failure() -> None:
        raise ServiceError(503, "database_unavailable", "Operation could not be completed")

    with TestClient(app) as client:
        assert client.get("/failure").status_code == 503

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert dict(spans[0].attributes or {})["error.type"] == "database_unavailable"
    runtime.shutdown()


def test_telemetry_disabled_keeps_service_behavior_and_hides_metrics() -> None:
    app = create_users_app(UsersSettings(metrics_enabled=False, otel_enabled=False))

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 404


def test_unreachable_trace_exporter_does_not_break_requests() -> None:
    app = create_users_app(
        UsersSettings(
            otel_enabled=True,
            otel_exporter_endpoint="http://127.0.0.1:1",
        )
    )

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_ground_truth_terms_are_absent_from_operational_metrics() -> None:
    metrics = ServiceMetrics("orders")
    metrics.observe_http("POST", "/orders", 503, 0.2)
    rendered = metrics.render().decode()
    prohibited = (
        "users_unavailable",
        "users_latency",
        "orders_unavailable",
        "orders_latency",
        "orders_database_unavailable",
        "expected_root_cause",
        "expected_unaffected_components",
        "difficulty",
        "failure_id",
    )

    assert all(term not in rendered for term in prohibited)


def test_database_health_is_a_single_low_cardinality_signal() -> None:
    metrics = ServiceMetrics("orders")
    metrics.set_database_health(False)
    rendered = metrics.render().decode()

    assert 'nexus_database_health{dependency="postgres",service="orders"} 0.0' in rendered
