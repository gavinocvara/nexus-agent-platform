"""Command Center as a Compose service: read-only, loopback, no secrets (ADR 0013).

Needs `docker compose --profile command-center up`. Without the profile the tests skip,
unless NEXUS_INTEGRATION_REQUIRE_COMMAND_CENTER=1 (CI), where a missing service fails.
These tests only read: they never write into the mounted state tree.
"""

import os
import time

import httpx
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def command_center_url() -> str:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 with the Compose environment running")
    url = os.getenv("NEXUS_INTEGRATION_COMMAND_CENTER_URL", "http://127.0.0.1:8765")
    try:
        httpx.get(f"{url}/healthz", timeout=5).raise_for_status()
    except httpx.HTTPError:
        if os.getenv("NEXUS_INTEGRATION_REQUIRE_COMMAND_CENTER") == "1":
            raise
        pytest.skip("Start the stack with `docker compose --profile command-center up`")
    return url


def _snapshot(url: str) -> dict[str, object]:
    response = httpx.get(f"{url}/api/v1/snapshot", timeout=10)
    response.raise_for_status()
    body = response.json()
    assert isinstance(body, dict)
    return body


def test_serves_the_client_and_a_sanitized_snapshot(command_center_url: str) -> None:
    index = httpx.get(f"{command_center_url}/", timeout=10)
    assert index.status_code == 200 and "<title>NEXUS Command Center</title>" in index.text
    assert "script-src 'self'" in index.headers["content-security-policy"]
    snapshot = _snapshot(command_center_url)
    assert snapshot["schema_version"] == 1
    assert snapshot["engineer_config"] is None, "the container never sees the engineer's env"
    assert snapshot["required_autonomous_gates"]
    brain = snapshot["memory"]["aegisops_brain"]  # type: ignore[index]
    assert brain["state"] == "disabled", "the brain stays outside Compose (ADR 0008)"


def test_reads_lab_health_over_the_compose_network(command_center_url: str) -> None:
    deadline = time.monotonic() + 60
    health: dict[str, object] = {}
    while time.monotonic() < deadline:
        health = _snapshot(command_center_url)["health"]  # type: ignore[assignment]
        if health.get("overall") == "healthy":
            break
        time.sleep(2)
    assert health.get("overall") == "healthy", health
    services = {item["service"] for item in health["services"]}  # type: ignore[attr-defined]
    assert {"gateway", "users", "orders", "postgres"} <= services


def test_replay_runs_inside_the_read_only_container(command_center_url: str) -> None:
    deadline = time.monotonic() + 60
    catalog: dict[str, object] = {}
    while time.monotonic() < deadline:
        catalog = httpx.get(f"{command_center_url}/api/v1/replay", timeout=10).json()
        if catalog.get("state") != "preparing":
            break
        time.sleep(2)
    assert catalog.get("state") == "ok", catalog
    assert len(catalog["episodes"]) == 8  # type: ignore[arg-type]


def test_refuses_writes_and_foreign_hosts(command_center_url: str) -> None:
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = httpx.request(method, f"{command_center_url}/api/v1/snapshot", timeout=10)
        assert response.status_code == 405, method
    foreign = httpx.get(
        f"{command_center_url}/api/v1/snapshot", headers={"Host": "attacker.example"}, timeout=10
    )
    assert foreign.status_code == 400


def test_stream_opens_with_a_snapshot(command_center_url: str) -> None:
    with httpx.stream("GET", f"{command_center_url}/api/v1/stream", timeout=10) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        received = ""
        for chunk in response.iter_text():
            received += chunk
            if "event: snapshot" in received:
                break
    assert received.startswith("retry: 3000") and "event: snapshot" in received
