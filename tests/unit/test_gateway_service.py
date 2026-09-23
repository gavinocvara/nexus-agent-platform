import json
from typing import Any

import httpx
from fastapi.testclient import TestClient

from nexus.config import GatewaySettings
from nexus.services.gateway.app import create_app


def _downstream(request: httpx.Request) -> httpx.Response:
    assert request.headers["X-Correlation-ID"] == "gateway-test"
    if request.url.path == "/users/1":
        return httpx.Response(
            200,
            json={"user_id": 1, "name": "Ada Lovelace", "email": "ada@example.test"},
        )
    if request.url.path == "/orders" and request.method == "POST":
        body: dict[str, Any] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "order_id": "75c23282-04bb-4c01-bcc5-840884075d1e",
                **body,
                "created_at": "2026-09-22T12:00:00Z",
            },
        )
    return httpx.Response(
        404,
        json={
            "error": {
                "code": "user_not_found",
                "message": "User was not found",
                "correlation_id": "gateway-test",
            }
        },
    )


def test_gateway_routes_users_and_orders_with_correlation_id() -> None:
    settings = GatewaySettings(
        users_service_url="http://users.test",
        orders_service_url="http://orders.test",
    )
    app = create_app(settings, httpx.MockTransport(_downstream))

    with TestClient(app) as client:
        health = client.get("/health")
        user = client.get("/users/1", headers={"X-Correlation-ID": "gateway-test"})
        order = client.post(
            "/orders",
            headers={"X-Correlation-ID": "gateway-test"},
            json={"user_id": 1, "item": "runbook", "quantity": 1},
        )

    assert health.status_code == 200
    assert user.status_code == 200
    assert user.json()["name"] == "Ada Lovelace"
    assert order.status_code == 201
    assert order.json()["item"] == "runbook"


def test_gateway_preserves_downstream_not_found_error() -> None:
    settings = GatewaySettings(users_service_url="http://users.test")
    app = create_app(settings, httpx.MockTransport(_downstream))

    with TestClient(app) as client:
        response = client.get("/users/999", headers={"X-Correlation-ID": "gateway-test"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "user_not_found",
            "message": "User was not found",
            "correlation_id": "gateway-test",
        }
    }
