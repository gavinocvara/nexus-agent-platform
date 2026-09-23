from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from nexus.config import OrdersSettings
from nexus.services.orders.app import create_app
from nexus.services.orders.database import Base, OrdersDatabase


@pytest.fixture
def orders_client(tmp_path: Path) -> Iterator[TestClient]:
    database = OrdersDatabase(f"sqlite:///{tmp_path / 'orders.db'}")
    Base.metadata.create_all(database.engine)
    app = create_app(OrdersSettings(database_url=f"sqlite:///{tmp_path / 'orders.db'}"), database)
    with TestClient(app) as client:
        yield client


def test_orders_health_includes_database(orders_client: TestClient) -> None:
    response = orders_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "orders",
        "status": "healthy",
        "dependencies": {"database": "healthy"},
    }


def test_order_creation_is_persisted_and_retrievable(orders_client: TestClient) -> None:
    created = orders_client.post(
        "/orders",
        json={"user_id": 1, "item": "diagnostic-runbook", "quantity": 2},
    )

    assert created.status_code == 201
    order = created.json()
    retrieved = orders_client.get(f"/orders/{order['order_id']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == order


def test_orders_validation_and_missing_order_errors(orders_client: TestClient) -> None:
    invalid = orders_client.post(
        "/orders",
        json={"user_id": 0, "item": "", "quantity": 0},
    )
    missing = orders_client.get(f"/orders/{uuid4()}")

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "order_not_found"
