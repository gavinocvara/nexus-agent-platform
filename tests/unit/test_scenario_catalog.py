import json
from pathlib import Path

import pytest

from nexus.lab.catalog import ScenarioCatalog, ScenarioCatalogError
from nexus.lab.schema import FailureClass


def test_repository_scenario_catalog_is_valid() -> None:
    catalog = ScenarioCatalog.load()

    assert [scenario.id for scenario in catalog.list()] == [
        "orders_database_unavailable",
        "orders_latency",
        "orders_unavailable",
        "users_latency",
        "users_unavailable",
    ]
    latency = catalog.get("users_latency")
    assert latency.fault.type is FailureClass.LATENCY
    assert latency.fault.delay_ms == 1500


def test_malformed_scenario_fails_clearly(tmp_path: Path) -> None:
    malformed = {
        "schema_version": 1,
        "id": "bad_latency",
        "fault": {"type": "latency"},
    }
    (tmp_path / "bad_latency.json").write_text(json.dumps(malformed), encoding="utf-8")

    with pytest.raises(ScenarioCatalogError, match="Invalid scenario"):
        ScenarioCatalog.load(tmp_path)


def test_unknown_scenario_is_rejected() -> None:
    catalog = ScenarioCatalog.load()

    with pytest.raises(ScenarioCatalogError, match="Unknown scenario"):
        catalog.get("not_a_scenario")
