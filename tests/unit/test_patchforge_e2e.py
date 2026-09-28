"""Deterministic PatchForge end-to-end harness and scenario catalog."""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from nexus.patchforge.e2e import (
    E2EScenario,
    FixtureRepository,
    PatchForgeE2EHarness,
    materialize_fixture,
)
from nexus.patchforge.e2e_catalog import CALCULATOR, default_catalog, patch_proposed
from nexus.patchforge.models import CheckStatus, PatchOutcome, ReproductionStatus
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

    assert run.result.outcome is scenario.expected_outcome
    assert run.result.failure is scenario.expected_failure
    assert run.workspace_removed
    assert run.completion.snapshot.phase.value == "closed"
    assert [item.sequence for item in run.result.tool_calls] == list(
        range(1, len(run.result.tool_calls) + 1)
    )
