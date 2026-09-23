from fastapi.testclient import TestClient

from nexus.config import UsersSettings
from nexus.services.users.app import create_app


def test_users_health() -> None:
    with TestClient(create_app(UsersSettings())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"service": "users", "status": "healthy"}
    assert response.headers["X-Correlation-ID"]


def test_users_lookup_and_not_found_error() -> None:
    with TestClient(create_app(UsersSettings())) as client:
        found = client.get("/users/1", headers={"X-Correlation-ID": "users-test"})
        missing = client.get("/users/999")

    assert found.status_code == 200
    assert found.json() == {
        "user_id": 1,
        "name": "Ada Lovelace",
        "email": "ada@example.test",
    }
    assert found.headers["X-Correlation-ID"] == "users-test"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "user_not_found"
