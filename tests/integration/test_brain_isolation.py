import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from nexus.brain.config import BrainSettings
from nexus.brain.manager import AegisOpsBrain
from nexus.brain.models import MemoryProvenance, MemoryRecord, MemoryType, ProceduralMemory
from nexus.brain.storage import SQLiteMemoryStore
from nexus.diagnostics.models import DatabaseHealthInput
from nexus.diagnostics.service import DiagnosticServiceLayer
from nexus.evaluation.aegisops.environment import BenchmarkWarmup, DockerComposeStackController

pytestmark = pytest.mark.integration

ORDERS = "http://localhost:8002"
PROMETHEUS = "http://localhost:9090"
LOKI = "http://localhost:3100"
TEMPO = "http://localhost:3200"
CANARY = "brain-telemetry-canary-v1"


@pytest.fixture(scope="module", autouse=True)
def require_compose() -> None:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 with the Compose environment running")


def _memory(note: str = "private-memory") -> MemoryRecord:
    now = datetime.now(UTC)
    return MemoryRecord(
        id=uuid4(),
        memory_type=MemoryType.PROCEDURAL,
        created_at=now,
        valid_from=now - timedelta(seconds=1),
        valid_to=now,
        payload=ProceduralMemory(
            trigger_terms=["investigate", "diagnostic"],
            candidate_next_steps=["get_database_health"],
            redundant_patterns=[note],
            source_episode_ids=[uuid4()],
        ),
        provenance=MemoryProvenance(agent_run_id=uuid4()),
    )


def test_compose_volume_reset_does_not_destroy_private_brain(tmp_path: Path) -> None:
    path = tmp_path / "brain.sqlite3"
    store = SQLiteMemoryStore(path)
    store.write(_memory())
    before = store.logical_sha256("aegisops.investigator")

    DockerComposeStackController(timeout_seconds=180).clean_restart()
    BenchmarkWarmup(warmup_seconds=6).run()

    after = SQLiteMemoryStore(path).logical_sha256("aegisops.investigator")
    assert after == before


def test_database_failure_does_not_disable_brain_or_expose_brain_queries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "brain.sqlite3"
    store = SQLiteMemoryStore(path)
    store.write(_memory())
    brain = AegisOpsBrain(BrainSettings(_env_file=None, mode="learn", path=path))

    reset = httpx.post(f"{ORDERS}/__lab/failures/reset-all", timeout=5)
    assert reset.status_code == 200
    try:
        activated = httpx.post(
            f"{ORDERS}/__lab/failures/activate",
            json={"failure_id": "orders_database_unavailable"},
            timeout=5,
        )
        assert activated.status_code == 200
        retrieval = asyncio.run(
            brain.retrieve("Investigate diagnostic evidence", as_of=datetime.now(UTC))
        )
        assert retrieval.metadata.retrieval_count == 1
        with DiagnosticServiceLayer() as diagnostics:
            database = diagnostics.get_database_health(DatabaseHealthInput())
        serialized = database.model_dump_json().lower()
        assert "brain" not in serialized
        assert str(path).lower() not in serialized
    finally:
        response = httpx.post(f"{ORDERS}/__lab/failures/reset-all", timeout=5)
        assert response.status_code == 200


def test_brain_activity_is_absent_from_investigator_visible_telemetry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "brain.sqlite3"
    store = SQLiteMemoryStore(path)
    store.write(_memory(CANARY))
    brain = AegisOpsBrain(BrainSettings(_env_file=None, mode="learn", path=path))
    retrieval = asyncio.run(
        brain.retrieve("Investigate diagnostic evidence", as_of=datetime.now(UTC))
    )
    assert retrieval.metadata.retrieval_count == 1

    metrics = httpx.get(f"{PROMETHEUS}/api/v1/label/__name__/values", timeout=5)
    metrics.raise_for_status()
    assert CANARY not in metrics.text
    assert not any("brain" in name.lower() for name in metrics.json()["data"])

    logs = httpx.get(
        f"{LOKI}/loki/api/v1/query_range",
        params={"query": f'{{service=~"gateway|users|orders"}} |= "{CANARY}"', "since": "10m"},
        timeout=5,
    )
    logs.raise_for_status()
    assert logs.json()["data"]["result"] == []

    traces = httpx.get(f"{TEMPO}/api/search", params={"limit": "100"}, timeout=5)
    traces.raise_for_status()
    assert CANARY not in traces.text
