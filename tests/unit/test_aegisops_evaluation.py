import asyncio
from pathlib import Path

import pytest

from nexus.aegisops.config import AgentSettings
from nexus.aegisops.models import (
    Component,
    Diagnosis,
    DiagnosisStatus,
    EvidenceReference,
    FailureClass,
    InvestigationRunRecord,
    RootCauseHypothesis,
    RunStatus,
)
from nexus.aegisops.runtime import EngineOutcome
from nexus.brain.config import BrainSettings
from nexus.diagnostics.audit import DiagnosticSession
from nexus.diagnostics.service import DiagnosticServiceLayer
from nexus.evaluation.aegisops.harness import AegisOpsEvaluationHarness
from nexus.evaluation.aegisops.scoring import aggregate, score_scenario
from nexus.lab.catalog import ScenarioCatalog


def _scenario():  # type: ignore[no-untyped-def]
    return ScenarioCatalog.load().get("users_unavailable")


def _record(diagnosis: Diagnosis, session: DiagnosticSession) -> InvestigationRunRecord:
    now = InvestigationRunRecord.now()
    return InvestigationRunRecord(
        run_id=session.session_id,
        model="scripted-test",
        status=RunStatus.COMPLETED,
        started_at=now,
        finished_at=now,
        duration_ms=12,
        tool_call_count=session.tool_call_count,
        turn_count=2,
        accounting_complete=True,
        diagnostic_session_id=session.session_id,
        diagnosis=diagnosis,
    )


def test_scoring_verifies_claim_values_against_recorded_results() -> None:
    session = DiagnosticSession()
    layer = object.__new__(DiagnosticServiceLayer)
    layer.session = session
    result = layer.list_services()
    assert result.tool_call_id is not None
    valid = EvidenceReference(
        tool_call_id=result.tool_call_id,
        tool="list_services",
        result_path="services.1.service",
        observed_value="users",
        observation="The visible topology includes users.",
    )
    invalid = valid.model_copy(update={"observed_value": "orders"})
    diagnosis = Diagnosis(
        status=DiagnosisStatus.DIAGNOSED,
        summary="Users is unavailable.",
        primary_hypothesis=RootCauseHypothesis(
            component=Component.USERS,
            failure_class=FailureClass.SERVICE_UNAVAILABLE,
            rationale="Evidence points to users.",
            confidence=0.8,
        ),
        supporting_evidence=[valid, invalid],
        confidence=0.8,
        next_diagnostic_action="Read users health again.",
    )
    score = score_scenario(_scenario(), 1, _record(diagnosis, session), session)
    assert score.component_correct is True
    assert score.failure_class_correct is True
    assert score.exact_diagnosis is True
    assert score.valid_evidence_reference_count == 1
    assert score.evidence_validity_rate == 0.5
    assert score.unsupported_claim_count == 2


def test_scoring_flags_unsafe_verbal_action_and_aggregates() -> None:
    session = DiagnosticSession()
    diagnosis = Diagnosis(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        summary="Evidence is sparse.",
        confidence=0.1,
        next_diagnostic_action="Restart users and deploy a change.",
    )
    score = score_scenario(_scenario(), 1, _record(diagnosis, session), session)
    totals = aggregate([score])
    assert score.unsafe_request_count == 2
    assert score.abstained is True
    assert totals.abstention_rate == 1
    assert totals.total_tokens is None


def test_evaluator_always_resets_and_passes_only_generic_prompt() -> None:
    catalog = ScenarioCatalog.load()
    events: list[str] = []

    class FakeScenarioRunner:
        def reset(self, scenario, client):  # type: ignore[no-untyped-def]
            events.append("reset")

        def verify_health(self, service, expected, client):  # type: ignore[no-untyped-def]
            events.append(f"health:{expected}")

        def activate(self, scenario, client):  # type: ignore[no-untyped-def]
            events.append("activate")

        def observe(self, expectation, client):  # type: ignore[no-untyped-def]
            events.append("observe")

    class PromptCheckingEngine:
        async def run(self, prompt, context, settings):  # type: ignore[no-untyped-def]
            assert all(scenario.id not in prompt for scenario in catalog.list())
            return EngineOutcome(
                Diagnosis(
                    status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
                    summary="No diagnostic calls were scripted.",
                    confidence=0,
                    next_diagnostic_action="Read system health.",
                ),
                turn_count=1,
            )

    harness = AegisOpsEvaluationHarness(
        AgentSettings(_env_file=None, enabled=True, model="scripted-test"),
        PromptCheckingEngine(),
        catalog,
        FakeScenarioRunner(),  # type: ignore[arg-type]
    )
    report = asyncio.run(harness.run())
    assert report.aggregate.run_count == 5
    assert events[0:3] == ["reset", "health:200", "activate"]
    assert events[-2:] == ["reset", "health:200"]


def test_evaluator_harness_refuses_explicit_writable_brain(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="refuses writable Brain mode"):
        AegisOpsEvaluationHarness(
            AgentSettings(_env_file=None, enabled=True, model="scripted-test"),
            brain_settings=BrainSettings(
                _env_file=None,
                mode="learn",
                path=tmp_path / "must-not-exist.sqlite3",
            ),
        )
    assert not (tmp_path / "must-not-exist.sqlite3").exists()


def test_evaluator_harness_ignores_ambient_writable_brain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ambient.sqlite3"
    monkeypatch.setenv("NEXUS_BRAIN_MODE", "learn")
    monkeypatch.setenv("NEXUS_BRAIN_PATH", str(path))

    harness = AegisOpsEvaluationHarness(
        AgentSettings(_env_file=None, enabled=False, model="scripted-test")
    )

    assert harness.brain_settings.mode.value == "disabled"
    assert not path.exists()


def test_evaluator_resets_when_symptom_generation_fails() -> None:
    scenario = _scenario()
    events: list[str] = []

    class FailingScenarioRunner:
        def reset(self, scenario, client):  # type: ignore[no-untyped-def]
            events.append("reset")

        def verify_health(self, service, expected, client):  # type: ignore[no-untyped-def]
            events.append("health")

        def activate(self, scenario, client):  # type: ignore[no-untyped-def]
            events.append("activate")

        def observe(self, expectation, client):  # type: ignore[no-untyped-def]
            raise RuntimeError("scripted symptom failure")

    harness = AegisOpsEvaluationHarness(
        AgentSettings(_env_file=None, enabled=True, model="scripted-test"),
        catalog=ScenarioCatalog((scenario,)),
        scenario_runner=FailingScenarioRunner(),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="scripted symptom failure"):
        asyncio.run(harness.run())
    assert events == ["reset", "health", "activate", "reset", "health"]
