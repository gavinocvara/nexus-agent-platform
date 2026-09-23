import os

import httpx
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def gateway_url() -> str:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 with the Compose environment running")
    return os.getenv("NEXUS_INTEGRATION_GATEWAY_URL", "http://localhost:8000")


def test_gateway_to_users_service(gateway_url: str) -> None:
    correlation_id = "integration-users"
    response = httpx.get(
        f"{gateway_url}/users/1",
        headers={"X-Correlation-ID": correlation_id},
        timeout=10,
    )

    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.test"
    assert response.headers["X-Correlation-ID"] == correlation_id


def test_gateway_to_orders_to_postgres_persists(gateway_url: str) -> None:
    correlation_id = "integration-orders"
    created = httpx.post(
        f"{gateway_url}/orders",
        headers={"X-Correlation-ID": correlation_id},
        json={"user_id": 1, "item": "integration-probe", "quantity": 3},
        timeout=10,
    )

    assert created.status_code == 201
    order = created.json()
    retrieved = httpx.get(
        f"{gateway_url}/orders/{order['order_id']}",
        headers={"X-Correlation-ID": correlation_id},
        timeout=10,
    )
    assert retrieved.status_code == 200
    assert retrieved.json() == order
    assert retrieved.headers["X-Correlation-ID"] == correlation_id
