"""Deterministic PatchForge end-to-end harness and scenario catalog."""

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from nexus.patchforge import e2e_catalog
from nexus.patchforge.e2e import (
    E2ERun,
    E2EScenario,
    FixtureRepository,
    PatchForgeE2EHarness,
    materialize_fixture,
)
from nexus.patchforge.e2e_catalog import CALCULATOR, default_catalog, patch_proposed
from nexus.patchforge.models import (
    CheckStatus,
    PatchForgeFailure,
    PatchOutcome,
    ReproductionStatus,
)
from nexus.patchforge.policy import CommandPurpose
from nexus.patchforge.workspace import GitRunner

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


def test_fixture_repositories_are_reproducible(tmp_path: Path) -> None:
    git = GitRunner(tmp_path / "git-runtime")
    first = materialize_fixture(CALCULATOR, tmp_path / "one", git, NOW)
    second = materialize_fixture(CALCULATOR, tmp_path / "two", git, NOW)
    assert first == second
    changed = FixtureRepository(name="changed", files={"a.txt": "a\n"})
    assert materialize_fixture(changed, tmp_path / "three", git, NOW) != first
    with pytest.raises(ValueError, match="timezone-aware"):
        materialize_fixture(CALCULATOR, tmp_path / "four", git, NOW.replace(tzinfo=None))


def test_happy_path_is_an_attested_patch_proposal(tmp_path: Path) -> None:
    run = PatchForgeE2EHarness(tmp_path, now=NOW).run(patch_proposed())

    assert run.matches_expectation
    result = run.result
    assert result.outcome is PatchOutcome.PATCH_PROPOSED
    assert result.reproduction is not None
    assert result.reproduction.status is ReproductionStatus.FAIL_BEFORE_PASS_AFTER
    assert all(item.status is CheckStatus.PASSED for item in result.checks)
    assert result.diff is not None and result.diff.base_sha == run.source_sha
    patch = run.artifacts.read(result.diff.patch_artifact)
    assert sha256(patch).hexdigest() == result.diff.diff_sha256
    assert b"-    return a + b\n+    return a - b\n" in patch
    assert result.to_atlas_result().changed_files == ["calculator.py"]


def test_scenarios_replay_to_byte_identical_results(tmp_path: Path) -> None:
    first = PatchForgeE2EHarness(tmp_path / "first", now=NOW).run(patch_proposed())
    second = PatchForgeE2EHarness(tmp_path / "second", now=NOW).run(patch_proposed())
    assert first.result_sha256 == second.result_sha256
    assert first.result == second.result


def test_harness_requires_an_aware_clock_and_fresh_scenario_roots(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        PatchForgeE2EHarness(tmp_path, now=NOW.replace(tzinfo=None))
    harness = PatchForgeE2EHarness(tmp_path, now=NOW)
    harness.run(patch_proposed())
    with pytest.raises(FileExistsError):
        harness.run(patch_proposed())


@pytest.mark.parametrize("scenario", default_catalog(), ids=lambda item: item.name)
def test_every_catalog_scenario_matches_its_expected_outcome(
    tmp_path: Path, scenario: E2EScenario
) -> None:
    run = PatchForgeE2EHarness(tmp_path, now=NOW).run(scenario)

    # Outcome, classification, phase, loops, findings, closed lifecycle with cleanup,
    # authoritative evidence, untouched source repository, no network/secrets/memory.
    assert run.problems() == []
    assert run.result.outcome is scenario.expected_outcome
    assert run.result.failure is scenario.expected_failure


def _run(tmp_path: Path, name: str) -> E2ERun:
    scenario = next(item for item in default_catalog() if item.name == name)
    return PatchForgeE2EHarness(tmp_path, now=NOW).run(scenario)


def test_bounded_retry_proposes_the_second_fix(tmp_path: Path) -> None:
    run = _run(tmp_path, "targeted_retry_succeeds")
    assert run.completion.snapshot.implementation_loops == 1
    assert run.result.diff is not None
    patch = run.artifacts.read(run.result.diff.patch_artifact)
    assert b"+    return a - b\n" in patch and b"b - a" not in patch


def test_finalization_reserve_is_usable_and_protected(tmp_path: Path) -> None:
    used = _run(tmp_path / "used", "finalization_reserve_used")
    assert used.result.budget_usage.finalization_reserve_used is True
    assert used.result.outcome is PatchOutcome.PATCH_PROPOSED

    protected = _run(tmp_path / "protected", "finalization_reserve_protected")
    finalize_calls = [
        item.tool_name.value
        for item in protected.result.tool_calls
        if item.phase.value == "finalize"
    ]
    # The second read was refused before execution, so it left no tool-call record.
    assert finalize_calls == ["git_status"]
    # Current Runtime semantics end finalization on that refusal, so no report is accepted.
    assert protected.completion.report is None


def test_unknown_report_evidence_is_refused_by_the_gateway(tmp_path: Path) -> None:
    run = _run(tmp_path, "unknown_report_evidence")
    last = run.result.tool_calls[-1]
    assert last.tool_name.value == "submit_report"
    assert last.status.value == "denied"
    assert run.completion.report is None


def test_tamper_and_stale_evidence_are_named_findings(tmp_path: Path) -> None:
    for name, code in (
        ("attestation_failed", "workspace_changed_out_of_band"),
        ("tamper_after_last_call", "final_state_unobserved"),
        ("stale_validation", "stale_validation"),
        ("reproduction_missing", "reproduction_missing"),
        ("reproduction_not_demonstrated", "reproduction_not_demonstrated"),
    ):
        run = _run(tmp_path / name, name)
        assert code in {item.code for item in run.result.policy_findings}, name


def test_sandbox_only_ever_receives_isolated_operator_commands(tmp_path: Path) -> None:
    run = _run(tmp_path, "patch_proposed")
    profile = run.scenario.profile
    assert [item.command for item in run.sandbox_requests] == [
        profile.commands[purpose]
        for purpose in (
            CommandPurpose.REPRODUCTION,
            CommandPurpose.TARGETED_TESTS,
            CommandPurpose.FULL_TEST_SUITE,
        )
    ]
    for request in run.sandbox_requests:
        assert request.policy.network_disabled and not request.policy.secrets_allowed
        assert not request.policy.git_directory_mounted


def test_invariant_checker_detects_a_changed_source_repository(tmp_path: Path) -> None:
    run = _run(tmp_path, "patch_proposed")
    tampered = replace(run, source_refs_after=run.source_refs_after + "extra\n")
    assert "source repository changed" in tampered.problems()


def test_catalog_covers_success_and_every_failure_classification() -> None:
    catalog = default_catalog()
    assert len({item.name for item in catalog}) == len(catalog)
    assert {item.expected_failure for item in catalog} == {None, *PatchForgeFailure}


def test_gate_passes_and_reports_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert e2e_catalog.run_gate(tmp_path / "ok") == []
    wrong = replace(patch_proposed(), expected_outcome=PatchOutcome.PARTIAL)
    monkeypatch.setattr(e2e_catalog, "default_catalog", lambda: [wrong])
    assert e2e_catalog.run_gate(tmp_path / "wrong") == ["patch_proposed: unexpected outcome"]
    assert e2e_catalog.main() == 1
    assert "FAILED patch_proposed" in capsys.readouterr().err
