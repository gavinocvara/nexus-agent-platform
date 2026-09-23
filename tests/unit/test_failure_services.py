from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient

from nexus.config import GatewaySettings, OrdersSettings, UsersSettings
from nexus.lab.failures import FailureController
from nexus.services.gateway.app import create_app as create_gateway_app
from nexus.services.orders.app import ORDER_FAILURES
from nexus.services.orders.app import create_app as create_orders_app
from nexus.services.orders.database import Base, OrdersDatabase
from nexus.services.users.app import USER_FAILURES
from nexus.services.users.app import create_app as create_users_app


def test_control_routes_do_not_exist_when_lab_mode_is_disabled(tmp_path: Path) -> None:
    users = create_users_app(UsersSettings(lab_failures_enabled=False))
    database_url = f"sqlite:///{tmp_path / 'disabled.db'}"
    database = OrdersDatabase(database_url)
    Base.metadata.create_all(database.engine)
    orders = create_orders_app(
        OrdersSettings(database_url=database_url, lab_failures_enabled=False),
        database,
    )
    gateway = create_gateway_app(GatewaySettings())

    for app in (users, orders, gateway):
        with TestClient(app) as client:
            assert client.get("/__lab/failures").status_code == 404


def test_users_unavailable_latency_and_recovery() -> None:
    controller = FailureController(USER_FAILURES)
    app = create_users_app(
        UsersSettings(lab_failures_enabled=True),
        failure_controller=controller,
    )

    with TestClient(app) as client:
        assert client.get("/users/1").status_code == 200
        activated = client.post(
            "/__lab/failures/activate",
            json={"failure_id": "users_unavailable"},
        )
        assert activated.status_code == 200
        assert client.get("/health").status_code == 503
        unavailable = client.get("/users/1")
        assert unavailable.status_code == 503
        assert unavailable.json()["error"]["code"] == "service_unavailable"
        assert "failure" not in unavailable.json()["error"]

        assert client.post("/__lab/failures/reset").json()["active"] is False
        assert client.get("/users/1").status_code == 200

        client.post(
            "/__lab/failures/activate",
            json={"failure_id": "users_latency"},
        )
        started = perf_counter()
        delayed = client.get("/users/1", headers={"X-Correlation-ID": "latency-test"})
        duration = perf_counter() - started
        assert delayed.status_code == 200
        assert duration >= 1.3
        assert delayed.headers["X-Correlation-ID"] == "latency-test"
        client.post("/__lab/failures/reset-all")
        assert client.get("/health").status_code == 200
        recovered_started = perf_counter()
        assert client.get("/users/1").status_code == 200
        assert perf_counter() - recovered_started < 1.0


def test_unknown_control_failure_is_rejected_without_state_change() -> None:
    app = create_users_app(UsersSettings(lab_failures_enabled=True))

    with TestClient(app) as client:
        response = client.post(
            "/__lab/failures/activate",
            json={"failure_id": "unknown"},
        )
        state = client.get("/__lab/failures/state")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_lab_failure"
    assert state.json() == {"active": False, "failure": None}


def test_orders_database_failure_and_recovery(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'orders-failure.db'}"
    database = OrdersDatabase(database_url)
    Base.metadata.create_all(database.engine)
    controller = FailureController(ORDER_FAILURES)
    app = create_orders_app(
        OrdersSettings(database_url=database_url, lab_failures_enabled=True),
        database,
        controller,
    )

    payload = {"user_id": 1, "item": "failure-probe", "quantity": 1}
    with TestClient(app) as client:
        baseline = client.post("/orders", json=payload)
        assert baseline.status_code == 201
        client.post(
            "/__lab/failures/activate",
            json={"failure_id": "orders_database_unavailable"},
        )
        health = client.get("/health")
        failed_write = client.post("/orders", json=payload)
        assert health.status_code == 503
        assert health.json()["dependencies"] == {"database": "unhealthy"}
        assert failed_write.status_code == 503
        assert failed_write.json()["error"]["code"] == "database_unavailable"

        client.post("/__lab/failures/reset")
        assert client.get("/health").status_code == 200
        assert client.post("/orders", json=payload).status_code == 201


def test_orders_unavailable_latency_and_recovery(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'orders-other-failures.db'}"
    database = OrdersDatabase(database_url)
    Base.metadata.create_all(database.engine)
    app = create_orders_app(
        OrdersSettings(database_url=database_url, lab_failures_enabled=True),
        database,
        FailureController(ORDER_FAILURES),
    )
    payload = {"user_id": 1, "item": "failure-probe", "quantity": 1}

    with TestClient(app) as client:
        client.post(
            "/__lab/failures/activate",
            json={"failure_id": "orders_unavailable"},
        )
        assert client.get("/health").status_code == 503
        unavailable = client.post("/orders", json=payload)
        assert unavailable.status_code == 503
        assert unavailable.json()["error"]["code"] == "service_unavailable"
        client.post("/__lab/failures/reset")
        assert client.get("/health").status_code == 200

        client.post(
            "/__lab/failures/activate",
            json={"failure_id": "orders_latency"},
        )
        started = perf_counter()
        delayed = client.post("/orders", json=payload)
        assert delayed.status_code == 201
        assert perf_counter() - started >= 1.3
        client.post("/__lab/failures/reset-all")
        recovered_started = perf_counter()
        assert client.post("/orders", json=payload).status_code == 201
        assert perf_counter() - recovered_started < 1.0
