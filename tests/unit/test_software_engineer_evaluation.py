"""The resident engineer's controlled evaluation catalog and gate."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus.software_engineer import evaluation
from nexus.software_engineer.evaluation import (
    EngineerEvaluationHarness,
    EngineerScenario,
    default_catalog,
)
from nexus.software_engineer.models import CycleDecision, NotificationEvent

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)


@pytest.mark.parametrize("scenario", default_catalog(), ids=lambda item: item.name)
def test_every_scenario_matches_its_expected_judgment(
    tmp_path: Path, scenario: EngineerScenario
) -> None:
    run = EngineerEvaluationHarness(tmp_path, now=NOW).run(scenario)
    assert run.problems() == [], run.record.decision_reasons


def test_catalog_covers_the_required_judgment_cases() -> None:
    names = {item.name for item in default_catalog()}
    required = {
        "obvious_micro_bug",
        "misleading_bug_report",
        "failing_unit_test",
        "unrelated_failing_test",
        "repeated_failed_patch",
        "risky_dependency_upgrade",
        "security_sensitive_change",
        "ambiguous_architectural_change",
        "unnecessary_refactor",
        "malformed_repository_state",
        "merge_conflict",
        "dirty_working_tree",
        "missing_slack_credentials",
        "slack_outage",
        "model_outage",
        "timeout",
        "insufficient_evidence",
        "conflicting_evidence",
        "stale_memory",
        "prompt_injection_in_history",
        "request_to_weaken_safety_controls",
        "benchmark_regression",
        "cost_budget_exhausted",
        "owner_rejection",
        "owner_approval",
        "owner_revision",
    }
    assert required <= names
    decisions = {item.expected_decision for item in default_catalog()}
    assert decisions == set(CycleDecision)


def test_only_the_obvious_micro_bug_ships_and_only_autonomously(tmp_path: Path) -> None:
    shipped = [item for item in default_catalog() if item.expected_decision is CycleDecision.SHIP]
    assert [item.name for item in shipped] == ["obvious_micro_bug"]
    run = EngineerEvaluationHarness(tmp_path, now=NOW).run(shipped[0])
    assert run.record.decision is CycleDecision.SHIP
    assert NotificationEvent.APPROVAL_REQUIRED not in {
        item.event for item in run.record.notifications
    }


def test_gate_passes_and_reports_a_wrong_expectation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert evaluation.run_gate(tmp_path / "ok", NOW) == []
    wrong = replace(default_catalog()[0], expected_decision=CycleDecision.SHIP)
    monkeypatch.setattr(evaluation, "default_catalog", lambda: [wrong])
    problems = evaluation.run_gate(tmp_path / "wrong", NOW)
    assert problems == ["nothing_to_do: decision no_work"]
    monkeypatch.setattr(evaluation, "run_gate", lambda root, now=NOW: ["x: broken"])
    assert evaluation.main() == 1
