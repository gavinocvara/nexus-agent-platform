"""Command Center: read-only sources, sanitized views, and the GET-only API (ADR 0013)."""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from nexus.atlas.models import ActorIdentity, ActorType
from nexus.command_center.app import CONTENT_SECURITY_POLICY, create_app
from nexus.command_center.config import CommandCenterSettings
from nexus.command_center.replay import CURATED, ReplayLibrary
from nexus.command_center.sanitize import WITHHELD, safe_text
from nexus.command_center.snapshot import SnapshotBuilder
from nexus.patchforge.canonical import canonical_json
from nexus.software_engineer.config import SoftwareEngineerSettings
from nexus.software_engineer.evaluation import (
    EVALUATION_TIME,
    EngineerEvaluationHarness,
    default_catalog,
)
from nexus.software_engineer.models import CyclePhase, CycleRecord, OwnerDecision, OwnerVerdict
from nexus.software_engineer.progress import CycleProgress, ProgressPublisher, progress_path

FAKE_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FAKE_WEBHOOK = "https://hooks.slack.com/services/T0000/B0000/" + "x" * 24
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def evaluated(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """State roots written by the real cycle runtime for three evaluation scenarios."""

    work = tmp_path_factory.mktemp("evaluated")
    catalog = {item.name: item for item in default_catalog()}
    harness = EngineerEvaluationHarness(work)
    roots: dict[str, Path] = {}
    for name in ("owner_approval", "autonomous_draft_publication", "unrelated_failing_test"):
        harness.run(catalog[name])
        roots[name] = work / name / "state"
    return roots


def _settings(state_root: Path, **overrides: object) -> CommandCenterSettings:
    values: dict[str, object] = {
        "engineer_state_root": state_root,
        "engineer_memory_path": state_root / "memory.sqlite3",
        "brain_path": state_root / "absent-brain.sqlite3",
        "scenario_directory": ROOT / "lab" / "scenarios" / "v1",
        "client_directory": state_root / "no-client",
        "health_enabled": False,
        "replay_enabled": False,
        "allowed_hosts": ["testserver"],
    }
    values.update(overrides)
    return CommandCenterSettings.model_validate(values)


def _engineer() -> SoftwareEngineerSettings:
    return SoftwareEngineerSettings.model_validate({})


def _builder(state_root: Path, **environment: str) -> SnapshotBuilder:
    return SnapshotBuilder(
        _settings(state_root), _engineer(), environment=lambda name: environment.get(name)
    )


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _latest(state_root: Path) -> CycleRecord:
    return CycleRecord.model_validate_json(
        (state_root / "cycles" / "latest.json").read_text(encoding="utf-8")
    )


# -- sanitization ------------------------------------------------------------------------


def test_safe_text_withholds_credentials_and_bounds_length() -> None:
    assert safe_text(f"token={FAKE_TOKEN}") == WITHHELD
    assert safe_text(FAKE_WEBHOOK) == WITHHELD
    bounded = safe_text("x" * 900, 100)
    assert len(bounded) == 100 and bounded.endswith("…")
    assert safe_text("ordinary text") == "ordinary text"


# -- snapshot ----------------------------------------------------------------------------


def test_empty_machine_reports_absent_sources_without_creating_files(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    snapshot = _builder(state_root).build(1)
    sources = {item.source: item.state for item in snapshot.sources}
    assert sources["engineer_state"] == "absent"
    assert sources["engineer_memory"] == "absent"
    assert sources["aegisops_brain"] == "absent"
    assert sources["lab_health"] == "disabled"
    assert snapshot.latest_cycle is None and snapshot.pending_approval is None
    assert snapshot.active_run is None
    assert {item.id: item.status for item in snapshot.systems}["engram"] == "not_built"
    assert not state_root.exists(), "reading must not create the state tree"


def test_pending_approval_shows_governed_channels_only(evaluated: dict[str, Path]) -> None:
    state_root = evaluated["owner_approval"]
    record = _latest(state_root)
    snapshot = _builder(state_root).build(1)
    pending = snapshot.pending_approval
    assert pending is not None and pending.status == "pending"
    assert pending.cycle_id == str(record.cycle_id)
    assert all(str(record.cycle_id) in line for line in pending.governed_channels)
    assert any(line.startswith("Slack  /nexus ") for line in pending.governed_channels)
    assert any("nexus.software_engineer decide" in line for line in pending.governed_channels)
    systems = {item.id: item for item in snapshot.systems}
    assert systems["resident_engineer"].status == "attention"
    assert systems["nexus"].status == "attention"


def test_recorded_owner_decision_resolves_the_question(
    evaluated: dict[str, Path], tmp_path: Path
) -> None:
    state_root = tmp_path / "state"
    _copy_tree(evaluated["owner_approval"], state_root)
    request = _latest(state_root).approval_request
    assert request is not None
    decision = OwnerDecision(
        decision_id=uuid4(),
        request_id=request.request_id,
        verdict=OwnerVerdict.SHIP,
        decided_by=ActorIdentity(actor_type=ActorType.HUMAN, actor_id="owner"),
        reason="looks right",
        channel="cli",
        decided_at=datetime(2026, 9, 30, tzinfo=UTC),
    )
    target = state_root / "decisions" / f"{request.request_id}.json"
    target.parent.mkdir(parents=True)
    target.write_text(canonical_json(decision) + "\n", encoding="utf-8")
    snapshot = _builder(state_root).build(2)
    assert snapshot.pending_approval is None
    assert [item.verdict for item in snapshot.decisions] == ["ship"]
    assert snapshot.latest_cycle is not None and snapshot.latest_cycle.approval is not None
    assert snapshot.latest_cycle.approval.status == "decided"


def test_transitions_name_only_the_systems_the_record_proves(evaluated: dict[str, Path]) -> None:
    shipped = _builder(evaluated["autonomous_draft_publication"]).build(1).latest_cycle
    assert shipped is not None
    actors = {item.target: item.actors for item in shipped.transitions}
    assert actors["implement"] == ["patchforge"]
    assert actors["test"] == ["patchforge", "sentinelqa"]
    assert actors["learn"] == ["memory"]
    assert shipped.published and shipped.change is not None
    assert shipped.change.publication is not None and shipped.change.publication.draft

    failed = _builder(evaluated["unrelated_failing_test"]).build(1).latest_cycle
    assert failed is not None and failed.decision == "abandon"
    assert any(item.gate == "pytest_full" and item.status == "failed" for item in failed.gates)


def test_memory_is_read_from_the_private_store(evaluated: dict[str, Path]) -> None:
    snapshot = _builder(evaluated["autonomous_draft_publication"]).build(1)
    engineer = snapshot.memory.engineer
    assert engineer.state == "ok" and engineer.total > 0
    assert engineer.by_status.get("validated_fact", 0) >= 1
    assert all(len(item.content) <= 280 for item in engineer.recent)


def test_active_lease_is_live_and_its_process_id_is_not_exposed(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    cycle_id = uuid4()
    now = datetime.now(UTC)
    (state_root / "run.lock").write_text(
        json.dumps(
            {
                "cycle_id": str(cycle_id),
                "started_at": now.isoformat(),
                "expires_at": now.replace(year=now.year + 1).isoformat(),
                "pid": 424242,
            }
        ),
        encoding="utf-8",
    )
    snapshot = _builder(state_root).build(1)
    assert snapshot.active_run is not None
    assert snapshot.active_run.cycle_id == str(cycle_id)
    assert snapshot.active_run.phase is None
    assert snapshot.active_run.phase_source == "unobservable"
    assert "424242" not in snapshot.model_dump_json()
    systems = {item.id: item.status for item in snapshot.systems}
    assert systems["resident_engineer"] == "active"
    assert systems["patchforge"] == "dormant", "a lease alone never proves PatchForge is working"


def test_payload_never_carries_secrets_env_values_or_ground_truth(
    evaluated: dict[str, Path], tmp_path: Path
) -> None:
    state_root = tmp_path / "state"
    _copy_tree(evaluated["owner_approval"], state_root)
    record = _latest(state_root)
    poisoned = record.model_copy(
        update={"decision_reasons": [*record.decision_reasons, f"use {FAKE_TOKEN} to push"]}
    )
    (state_root / "cycles" / f"{record.cycle_id}.json").write_text(
        canonical_json(poisoned) + "\n", encoding="utf-8"
    )
    engineer = _engineer()
    builder = _builder(
        state_root,
        **{engineer.slack_webhook_env: FAKE_WEBHOOK, engineer.github_token_env: FAKE_TOKEN},
    )
    snapshot = builder.build(1)
    payload = snapshot.model_dump_json()
    assert FAKE_TOKEN not in payload and FAKE_WEBHOOK not in payload
    assert WITHHELD in payload
    assert snapshot.engineer_config is not None
    assert snapshot.engineer_config.slack_webhook_present is True
    assert snapshot.engineer_config.github_token_present is True
    # Lab scenarios carry evaluator ground truth; only identity may leave the server.
    for path in (ROOT / "lab" / "scenarios" / "v1").glob("*.json"):
        truth = json.loads(path.read_text(encoding="utf-8"))["expected_root_cause"]
        assert truth["failure"] not in payload
    assert "expected_root_cause" not in payload and "expected_symptoms" not in payload
    assert len(snapshot.lab_scenarios) == 5


# -- API ---------------------------------------------------------------------------------


def _client(state_root: Path, **overrides: object) -> TestClient:
    return TestClient(
        create_app(
            _settings(state_root, **overrides),
            engineer=_engineer(),
            health_reader=None,
            background=False,
        )
    )


def test_every_route_is_read_only(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), engineer=_engineer(), background=False)
    methods: set[str] = set()
    for route in app.routes:
        methods |= set(getattr(route, "methods", None) or set())
    assert methods <= {"GET", "HEAD"}, methods


def test_api_serves_views_and_leaves_state_untouched(
    evaluated: dict[str, Path], tmp_path: Path
) -> None:
    state_root = tmp_path / "state"
    _copy_tree(evaluated["owner_approval"], state_root)
    before = _tree_digest(state_root)
    record = _latest(state_root)
    with _client(state_root) as client:
        snapshot = client.get("/api/v1/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert snapshot.headers["cache-control"] == "no-store"
        assert snapshot.json()["pending_approval"]["cycle_id"] == str(record.cycle_id)
        cycles = client.get("/api/v1/cycles").json()
        assert [item["cycle_id"] for item in cycles] == [str(record.cycle_id)]
        detail = client.get(f"/api/v1/cycles/{record.cycle_id}")
        assert detail.status_code == 200 and detail.json()["report_text"]
        assert client.get(f"/api/v1/cycles/{uuid4()}").status_code == 404
        assert client.get("/api/v1/cycles/not-a-uuid").status_code == 422
        assert client.post("/api/v1/snapshot").status_code == 405
        assert client.get("/").status_code == 200
    assert _tree_digest(state_root) == before


def test_foreign_host_header_is_refused(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/v1/snapshot", headers={"host": "attacker.example"})
        assert response.status_code == 400


def test_replay_episodes_carry_the_evaluation_record_hash(tmp_path: Path) -> None:
    library = ReplayLibrary(tuple(item for item in CURATED if item.name == "owner_approval"))
    library.capture()
    catalog = library.catalog()
    assert catalog.state == "ok" and [item.name for item in catalog.episodes] == ["owner_approval"]
    episode = library.episode("owner_approval")
    assert episode is not None
    scenario = next(item for item in default_catalog() if item.name == "owner_approval")
    expected = EngineerEvaluationHarness(tmp_path).run(scenario).record_sha256
    assert episode.record_sha256 == expected
    assert episode.owner_step is not None and episode.owner_step.verdict == "ship"
    assert episode.scripted_systems == ["patchforge", "sentinelqa"]
    assert "scripted" in episode.provenance and "synthetic" in episode.tempo_note
    app = create_app(
        _settings(tmp_path, replay_enabled=True),
        engineer=_engineer(),
        replay=library,
        health_reader=None,
        background=False,
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/replay").json()["state"] == "ok"
        assert client.get("/api/v1/replay/owner_approval").status_code == 200
        assert client.get("/api/v1/replay/Owner-Approval").status_code == 404
        assert client.get("/api/v1/replay/missing").status_code == 404


def test_serving_modules_cannot_reach_write_paths() -> None:
    """The view layer may read records; it must never import anything that decides,
    publishes, notifies, executes, or calls a model."""

    forbidden = {
        "nexus.software_engineer.approval",
        "nexus.software_engineer.publish",
        "nexus.software_engineer.notify",
        "nexus.software_engineer.slack_commands",
        "nexus.software_engineer.exercise",
        "nexus.software_engineer.executor",
        "nexus.software_engineer.cycle",
        "nexus.software_engineer.sandbox",
        "nexus.patchforge.live",
        "nexus.atlas.service",
        "openai",
        "agents",
    }
    package = ROOT / "src" / "nexus" / "command_center"
    for path in package.glob("*.py"):
        if path.name in {"replay.py", "__main__.py"}:
            continue  # replay runs the evaluation harness in a temporary directory
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not any(name == item or name.startswith(item + ".") for item in forbidden), (
                    f"{path.name} imports {name}"
                )


def _copy_tree(source: Path, target: Path) -> None:
    for path in source.rglob("*"):
        destination = target / path.relative_to(source)
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())


# -- live phase (progress file written by the cycle runtime) -----------------------------


def _live_lease(state_root: Path, cycle_id: UUID, *, minutes: int = 60) -> None:
    now = datetime.now(UTC)
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "run.lock").write_text(
        json.dumps(
            {
                "cycle_id": str(cycle_id),
                "started_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=minutes)).isoformat(),
                "pid": 1,
            }
        ),
        encoding="utf-8",
    )


def _progress(state_root: Path, cycle_id: UUID, phase: CyclePhase, executor: str) -> None:
    now = datetime.now(UTC)
    path = progress_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        CycleProgress(
            cycle_id=cycle_id,
            sequence=6,
            phase=phase,
            executor=executor,  # type: ignore[arg-type]
            started_at=now,
            updated_at=now,
        ).model_dump_json(),
        encoding="utf-8",
    )


def test_implement_with_patchforge_is_a_pipeline_not_two_active_systems(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    cycle_id = uuid4()
    _live_lease(state_root, cycle_id)
    _progress(state_root, cycle_id, CyclePhase.IMPLEMENT, "patchforge")
    snapshot = _builder(state_root).build(1)
    run = snapshot.active_run
    assert run is not None and run.phase_source == "progress_file"
    assert (run.phase, run.stage, run.sequence) == ("implement", "IMPLEMENT", 6)
    assert run.actors == ["resident_engineer"]
    assert run.pipeline == ["patchforge", "sentinelqa"]
    assert run.description is not None and "not observable" in run.description
    systems = {item.id: item.status for item in snapshot.systems}
    assert systems["patchforge"] == "pipeline" and systems["sentinelqa"] == "pipeline"
    assert systems["resident_engineer"] == "active"


def test_dry_run_implement_and_understand_light_only_what_runs(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    cycle_id = uuid4()
    _live_lease(state_root, cycle_id)
    _progress(state_root, cycle_id, CyclePhase.IMPLEMENT, "dry_run")
    snapshot = _builder(state_root).build(1)
    assert snapshot.active_run is not None and snapshot.active_run.pipeline == []
    systems = {item.id: item.status for item in snapshot.systems}
    assert systems["patchforge"] == "dormant" and systems["sentinelqa"] == "dormant"
    _progress(state_root, cycle_id, CyclePhase.UNDERSTAND, "dry_run")
    snapshot = _builder(state_root).build(2)
    assert {item.id: item.status for item in snapshot.systems}["memory"] == "active"


def test_progress_is_ignored_without_its_own_live_lease(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    holder, stranger = uuid4(), uuid4()
    _live_lease(state_root, holder)
    _progress(state_root, stranger, CyclePhase.IMPLEMENT, "patchforge")
    run = _builder(state_root).build(1).active_run
    assert run is not None and run.phase is None and run.phase_source == "unobservable"
    _live_lease(state_root, stranger, minutes=-5)  # expired: the cycle died
    snapshot = _builder(state_root).build(2)
    assert snapshot.active_run is None
    assert {item.id: item.status for item in snapshot.systems}["patchforge"] == "dormant"


def test_the_dashboard_sees_the_real_phase_sequence_while_a_cycle_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[str | None, str | None]] = []
    original = ProgressPublisher.publish

    def spy(self: ProgressPublisher, **kwargs: object) -> None:
        original(self, **kwargs)  # type: ignore[arg-type]
        state_root = self.path.parent.parent
        before = _tree_digest(state_root)
        # The evaluation cycle runs on a fixed clock; observe on the same one.
        builder = SnapshotBuilder(_settings(state_root), _engineer(), clock=lambda: EVALUATION_TIME)
        run = builder.build(1).active_run
        assert _tree_digest(state_root) == before, "observing never writes"
        observed.append((run.stage, run.phase_source) if run else (None, None))

    monkeypatch.setattr(ProgressPublisher, "publish", spy)
    scenario = next(item for item in default_catalog() if item.name == "owner_approval")
    record = EngineerEvaluationHarness(tmp_path).run(scenario).record
    expected = [
        (transition.target.value.upper(), "progress_file") for transition in record.transitions
    ]
    stages = {"self_review": "REVIEW", "assess_risk": "RISK", "observe_results": "VERIFY"}
    expected = [(stages.get(stage.lower(), stage), source) for stage, source in expected]
    assert observed == expected
    assert not progress_path(tmp_path / "owner_approval" / "state").exists()


def test_a_deployment_without_the_engineer_environment_says_so(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "state", engineer_config_visible=False)
    snapshot = SnapshotBuilder(settings, _engineer()).build(1)
    assert snapshot.engineer_config is None
    assert snapshot.required_autonomous_gates, "the ship policy is code and stays visible"
    resident = {item.id: item for item in snapshot.systems}["resident_engineer"]
    assert {fact.label: fact.value for fact in resident.facts}["Enabled"] == "NOT VISIBLE"


def test_a_deployment_that_does_not_read_the_brain_never_opens_it(tmp_path: Path) -> None:
    brain_path = tmp_path / "brain.sqlite3"
    brain_path.write_bytes(b"not a database")  # opening it would read as unreadable
    enabled = SnapshotBuilder(_settings(tmp_path / "state", brain_path=brain_path), _engineer())
    assert enabled.build(1).memory.aegisops_brain.state == "unreadable"
    settings = _settings(tmp_path / "state", brain_path=brain_path, brain_enabled=False)
    snapshot = SnapshotBuilder(settings, _engineer()).build(1)
    assert snapshot.memory.aegisops_brain.state == "disabled"
    source = {item.source: item for item in snapshot.sources}["aegisops_brain"]
    assert source.detail == "Not read by this deployment"
    systems = {item.id: item for item in snapshot.systems}
    assert {fact.label: fact.value for fact in systems["aegisops"].facts}[
        "Investigator memory"
    ] == "NOT VISIBLE"
    assert brain_path.read_bytes() == b"not a database"
