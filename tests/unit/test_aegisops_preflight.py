import importlib.metadata
import json

import httpx
import pytest

from nexus.aegisops.config import AgentSettings
from nexus.brain.config import BrainSettings
from nexus.brain.storage import SQLiteMemoryStore
from nexus.evaluation.aegisops import identity as identity_module
from nexus.evaluation.aegisops.benchmark_models import BaselineIdentity
from nexus.evaluation.aegisops.identity import (
    build_baseline_identity,
    canonical_hash,
    diagnosis_schema_hash,
    scenario_catalog_hash,
)
from nexus.evaluation.aegisops.preflight import BenchmarkPreflight
from nexus.lab.catalog import ScenarioCatalog

PHASE_5_INSTRUCTION_HASH = "d858a63116e8579a4e19a19f01cea5441aa840213380455449528ca385dad456"
PHASE_5_TOOL_REGISTRY_HASH = "44292aa3ded19cf0e03f086be6716d51099efa78eb97e3517799b713175ff240"
REPAIRED_TOOL_REGISTRY_HASH = "b75d71fff306ab51f445eba5d9a438dc394d02c810ec7fceb4110975a15cbfbf"
PHASE_5_DOMAIN_DIAGNOSIS_SCHEMA_HASH = (
    "f10168000946760b57a11fadf51efb86566f689e90a338050b0bc7d5c5c4e455"
)
REPAIRED_DIAGNOSIS_OUTPUT_SCHEMA_HASH = (
    "aea17a6803fe3c49f1d1e3268b5f58072e05b5edd5dce9fb72514b96a5b720ab"
)
PHASE_5_SCENARIO_CATALOG_HASH = "6b47e28ee3d5c13619903d9885212022c49940224e333188d177a40e46214e2c"


def _identity() -> BaselineIdentity:
    return BaselineIdentity(
        git_sha="a" * 40,
        git_dirty=False,
        reproducible=True,
        nexus_version="0.7.3",
        model="fixture-model",
        instruction_hash=PHASE_5_INSTRUCTION_HASH,
        tool_registry_hash=REPAIRED_TOOL_REGISTRY_HASH,
        diagnosis_schema_hash=REPAIRED_DIAGNOSIS_OUTPUT_SCHEMA_HASH,
        scenario_catalog_hash=PHASE_5_SCENARIO_CATALOG_HASH,
        max_turns=10,
        max_tool_calls=12,
        timeout_seconds=120,
        agents_sdk_version="0.22.3",
        python_version="3.12.1",
        platform="test",
    )


def test_frozen_behavior_and_repaired_output_schema_hashes() -> None:
    from nexus.aegisops.instructions import instruction_hash
    from nexus.aegisops.models import Diagnosis
    from nexus.aegisops.tools import tool_registry_hash

    catalog = ScenarioCatalog.load()
    assert instruction_hash() == PHASE_5_INSTRUCTION_HASH
    assert tool_registry_hash() == REPAIRED_TOOL_REGISTRY_HASH
    assert canonical_hash(Diagnosis.model_json_schema()) == PHASE_5_DOMAIN_DIAGNOSIS_SCHEMA_HASH
    assert diagnosis_schema_hash() == REPAIRED_DIAGNOSIS_OUTPUT_SCHEMA_HASH
    assert scenario_catalog_hash(catalog) == PHASE_5_SCENARIO_CATALOG_HASH
    assert importlib.metadata.version("openai-agents") == "0.22.3"


def test_dirty_tree_is_refused_or_marked_non_reproducible(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(identity_module, "repository_state", lambda: ("a" * 40, True))
    settings = AgentSettings(_env_file=None, model="fixture-model")
    catalog = ScenarioCatalog.load()
    with pytest.raises(ValueError, match="clean Git working tree"):
        build_baseline_identity(settings, catalog)
    identity = build_baseline_identity(settings, catalog, allow_dirty=True)
    assert identity.git_dirty is True
    assert identity.reproducible is False


def test_preflight_checks_every_boundary_without_serializing_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENAI_API_KEY", "secret-test-value")
    monkeypatch.setattr(
        "nexus.evaluation.aegisops.preflight.repository_state",
        lambda: ("a" * 40, False),
    )
    monkeypatch.setattr(
        "nexus.evaluation.aegisops.preflight.build_baseline_identity",
        lambda settings, catalog, allow_dirty=False, brain_settings=None: _identity(),
    )
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"ok": True}))
    report = BenchmarkPreflight(
        AgentSettings(_env_file=None, enabled=True, model="gpt-5.6-sol"),
        transport=transport,
    ).run()
    assert report.ready is True
    names = {check.name for check in report.checks}
    assert names >= {
        "openai_api_key",
        "gateway_health",
        "prometheus",
        "loki",
        "tempo",
        "users_lab_control",
        "orders_lab_control",
        "tool_policy_boundary",
    }
    serialized = report.model_dump_json()
    assert "secret-test-value" not in serialized
    assert "Authorization" not in serialized
    assert "OPENAI_API_KEY" not in serialized


def test_preflight_missing_key_and_backends_prevent_execution(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "nexus.evaluation.aegisops.preflight.repository_state",
        lambda: ("a" * 40, False),
    )
    transport = httpx.MockTransport(lambda _request: httpx.Response(503))
    report = BenchmarkPreflight(
        AgentSettings(_env_file=None, enabled=False), transport=transport
    ).run()
    assert report.ready is False
    failed = {check.name for check in report.checks if check.status == "failed"}
    assert "live_agent_enabled" in failed
    assert "openai_api_key" in failed
    assert "prometheus" in failed
    assert "users_lab_control" in failed


def test_identity_json_contains_no_environment_or_secrets() -> None:
    serialized = json.dumps(_identity().model_dump(mode="json"))
    for forbidden in ("OPENAI_API_KEY", "Authorization", "POSTGRES_PASSWORD", "secret"):
        assert forbidden not in serialized


def test_brain_identity_is_explicit_without_changing_frozen_behavior_hashes(
    tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "brain.sqlite3"
    store = SQLiteMemoryStore(path)
    store.initialize()
    digest = store.logical_sha256("aegisops.investigator")
    monkeypatch.setattr(identity_module, "repository_state", lambda: ("a" * 40, False))
    identity = build_baseline_identity(
        AgentSettings(_env_file=None, enabled=True, model="fixture-model"),
        ScenarioCatalog.load(),
        brain_settings=BrainSettings(
            _env_file=None,
            mode="frozen_eval",
            path=path,
            expected_snapshot_sha256=digest,
        ),
    )
    assert identity.baseline_name == "aegisops-brain-v1"
    assert identity.brain_enabled is True
    assert identity.brain_read_only is True
    assert identity.brain_identity.mode == "frozen_eval"
    assert identity.brain_identity.writer_allowlist_sha256 is not None
    assert identity.brain_identity.preregistration_sha256 is not None
    assert identity.brain_memory_sha256 is not None
    assert len(identity.brain_memory_sha256) == 64
    assert identity.instruction_hash == PHASE_5_INSTRUCTION_HASH
    assert identity.tool_registry_hash == REPAIRED_TOOL_REGISTRY_HASH
    assert identity.diagnosis_schema_hash == REPAIRED_DIAGNOSIS_OUTPUT_SCHEMA_HASH
    assert identity.scenario_catalog_hash == PHASE_5_SCENARIO_CATALOG_HASH
