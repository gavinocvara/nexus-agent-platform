"""SentinelQA adversarial catalog: every scenario, its invariants, and byte-identical replay."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus.atlas.models import ReviewVerdict
from nexus.sentinelqa import catalog
from nexus.sentinelqa.catalog import default_catalog, honest_fix
from nexus.sentinelqa.harness import SentinelQAHarness, SentinelScenario
from nexus.sentinelqa.models import (
    FindingCategory,
    SentinelFindingCode,
    SpecificationTree,
)

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)


@pytest.mark.parametrize("scenario", default_catalog(), ids=lambda item: item.name)
def test_every_scenario_matches_its_expected_verdict(
    tmp_path: Path, scenario: SentinelScenario
) -> None:
    run = SentinelQAHarness(tmp_path, now=NOW).run(scenario)
    assert run.problems() == []
    assert run.verdict.verdict is scenario.expected_verdict


def test_catalog_covers_every_fail_closed_case_from_the_brief() -> None:
    covered: set[str] = set()
    verdicts: set[ReviewVerdict] = set()
    for scenario in default_catalog():
        covered.update(scenario.expected_findings)
        verdicts.add(scenario.expected_verdict)
    required = {
        SentinelFindingCode.SPECIFICATION_MODIFIED,  # tests modified, assertions weakened
        SentinelFindingCode.SPECIFICATION_DELETED,  # tests deleted, renamed, moved
        SentinelFindingCode.EVALUATION_CONFIG_ALTERED,  # evaluator configuration altered
        SentinelFindingCode.TESTS_SKIPPED,  # tests skipped or disabled
        SentinelFindingCode.TESTS_MISSING,  # tests not collected
        SentinelFindingCode.PRISTINE_TESTS_FAILED,  # succeeds only because tests changed
        SentinelFindingCode.INDEPENDENT_VALIDATION_DISAGREES,
        SentinelFindingCode.VERIFICATION_TREE_MUTATED,  # evaluation boundary bypassed
        SentinelFindingCode.PRISTINE_REFERENCE_UNVERIFIABLE,  # pristine identity unverifiable
        SentinelFindingCode.IDENTITY_MISMATCH,
        SentinelFindingCode.EVIDENCE_INCOMPLETE,  # required evidence incomplete
        SentinelFindingCode.EXECUTOR_UNAVAILABLE,  # validation cannot be trusted
        SentinelFindingCode.NOT_A_PROPOSAL,
        SentinelFindingCode.NO_CODE_CHANGE,
    }
    assert {code.value for code in required} <= covered
    assert verdicts == {ReviewVerdict.PASSED, ReviewVerdict.FAILED, ReviewVerdict.INCONCLUSIVE}


def test_honest_fix_replays_byte_identically_and_records_five_runs(tmp_path: Path) -> None:
    first = SentinelQAHarness(tmp_path / "first", now=NOW).run(honest_fix())
    second = SentinelQAHarness(tmp_path / "second", now=NOW).run(honest_fix())
    assert first.verdict_sha256 == second.verdict_sha256
    verdict = first.verdict
    assert verdict.verdict is ReviewVerdict.PASSED
    assert [item.tree for item in verdict.runs] == [
        SpecificationTree.PRISTINE,
        SpecificationTree.PRISTINE,
        SpecificationTree.PRISTINE,
        SpecificationTree.VERIFICATION,
        SpecificationTree.VERIFICATION,
    ]
    assert all(item.tree_unchanged for item in verdict.runs)
    assert verdict.patchforge_checks_agree is True
    assert verdict.integrity is not None
    assert verdict.integrity.locked_entries == 1
    assert verdict.integrity.candidate_changed_files == ["calculator.py"]
    assert verdict.integrity.verification_changed_files == ["calculator.py"]
    assert verdict.lock_sha256 == first.lock.lock_sha256
    # SentinelQA left the operator's source repository untouched.
    assert first.source_refs_before == first.source_refs_after


def test_specification_rewrite_is_rejected_without_ground_truth(tmp_path: Path) -> None:
    run = SentinelQAHarness(tmp_path, now=NOW).run(catalog.fix_and_weaken_test())
    verdict = run.verdict
    assert verdict.verdict is ReviewVerdict.FAILED
    assert verdict.integrity is not None
    assert verdict.integrity.modified == ["tests/test_calculator.py"]
    assert verdict.integrity.restored == ["tests/test_calculator.py"]
    # The code fix itself was right, so the pristine tests pass on the verification tree;
    # the rejection comes from the specification change alone.
    assert verdict.patchforge_checks_agree is True
    assert {item.code for item in verdict.findings} == {SentinelFindingCode.SPECIFICATION_MODIFIED}
    assert "rejected" in verdict.summary and "specification_modified" in verdict.summary


def test_candidate_only_passing_with_its_own_tests_is_exposed(tmp_path: Path) -> None:
    run = SentinelQAHarness(tmp_path, now=NOW).run(catalog.wrong_fix_with_rewritten_test())
    verdict = run.verdict
    assert verdict.verdict is ReviewVerdict.FAILED
    assert verdict.patchforge_checks_agree is False
    disagreement = next(
        item
        for item in verdict.findings
        if item.code is SentinelFindingCode.INDEPENDENT_VALIDATION_DISAGREES
    )
    assert "only with its own changes to the specification" in disagreement.detail


def test_unverifiable_evidence_never_passes_even_with_a_good_candidate(tmp_path: Path) -> None:
    for scenario in (
        catalog.lock_digest_tampered(),
        catalog.artifact_corrupted(),
        catalog.executor_unavailable(),
    ):
        run = SentinelQAHarness(tmp_path / scenario.name, now=NOW).run(scenario)
        assert run.verdict.verdict is ReviewVerdict.INCONCLUSIVE
        assert all(item.category is FindingCategory.UNVERIFIABLE for item in run.verdict.findings)
        # Nothing ran on an untrusted reference.
        assert not any(item.tree is SpecificationTree.VERIFICATION for item in run.verdict.runs)


def test_gate_passes_and_reports_a_wrong_expectation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert catalog.run_gate(tmp_path / "ok", NOW) == []
    wrong = replace(honest_fix(), expected_verdict=ReviewVerdict.FAILED)
    monkeypatch.setattr(catalog, "default_catalog", lambda: [wrong])
    assert catalog.run_gate(tmp_path / "wrong", NOW) == ["honest_fix: unexpected verdict"]


def test_main_returns_nonzero_on_problems(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog, "run_gate", lambda root, now=NOW: ["x: broken"])
    assert catalog.main() == 1
    monkeypatch.setattr(catalog, "run_gate", lambda root, now=NOW: [])
    assert catalog.main() == 0
