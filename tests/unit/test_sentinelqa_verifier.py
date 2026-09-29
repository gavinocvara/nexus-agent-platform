"""SentinelQA verifier against forged or hostile PatchForge evidence.

SentinelQA does not trust PatchForge's attestation. These tests take a genuine
``patch_proposed`` run and forge parts of its result the way a compromised PatchForge, a
tampered artifact store, or an attacker between the two would, then check that SentinelQA
judges the *actual* patch, not the claims about it.
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from nexus.atlas.models import ArtifactReference, ReviewVerdict
from nexus.patchforge.attestor import PATCH_ARTIFACT_TYPE, LocalArtifactStore
from nexus.patchforge.e2e import E2ERun, PatchForgeE2EHarness
from nexus.patchforge.e2e_catalog import FIXED, patch_proposed
from nexus.patchforge.models import DiffSummary, PatchResult
from nexus.patchforge.sandbox import FakeSandbox, SandboxExecutor
from nexus.patchforge.workspace import GitRunner
from nexus.sentinelqa.catalog import honest_plans
from nexus.sentinelqa.lock import capture_specification_lock
from nexus.sentinelqa.models import (
    SENTINELQA_AGENT_ID,
    SentinelFindingCode,
    SentinelVerdict,
    SpecificationTree,
)
from nexus.sentinelqa.verifier import ArtifactReader, SentinelQAVerifier

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
REVIEW_ID = UUID("11111111-2222-4333-8444-555555555555")


@pytest.fixture
def candidate(tmp_path: Path) -> E2ERun:
    return PatchForgeE2EHarness(tmp_path / "patchforge", now=NOW).run(patch_proposed())


def _git_patch(git: GitRunner, source: Path, mutate: Callable[[Path], None]) -> bytes:
    """Produce a Git binary patch of ``mutate`` against the fixture's committed tree."""

    scratch = source.parent / "scratch"
    git.run(["clone", "--quiet", "--no-hardlinks", "--", str(source), str(scratch)])
    mutate(scratch)
    location = ["-C", str(scratch), "-c", "core.autocrlf=false"]
    git.run([*location, "add", "--intent-to-add", "--all", "--", "."])
    return git.run(
        [*location, "diff", "--binary", "--no-ext-diff", "--full-index", "HEAD", "--"]
    ).stdout


def _forge(run: E2ERun, patch: bytes, changed_files: list[str]) -> PatchResult:
    """Replace the attested patch with another one while keeping the result well-formed."""

    diff = run.result.diff
    assert diff is not None
    artifact = run.artifacts.put(patch, artifact_type=PATCH_ARTIFACT_TYPE)
    forged = DiffSummary(
        base_sha=diff.base_sha,
        proposed_head_sha=diff.proposed_head_sha,
        diff_sha256=artifact.sha256,
        patch_artifact=artifact,
        changed_files=changed_files,
        additions=diff.additions,
        deletions=diff.deletions,
    )
    return run.result.model_copy(update={"diff": forged})


def _review(
    tmp_path: Path,
    run: E2ERun,
    result: PatchResult | None = None,
    *,
    sandbox: SandboxExecutor | None = None,
    artifacts: ArtifactReader | None = None,
) -> SentinelVerdict:
    git = GitRunner(tmp_path / "git-sentinel")
    source = run.root / "source"
    profile = run.scenario.profile
    lock = capture_specification_lock(git, source, run.source_sha, profile, captured_at=NOW)
    verifier = SentinelQAVerifier(
        task=run.task,
        profile=profile,
        lock=lock,
        artifact_store=artifacts or run.artifacts,
        sandbox=sandbox or FakeSandbox(honest_plans(), clock=lambda: NOW),
        git=git,
        clock=lambda: NOW,
        review_id=REVIEW_ID,
        work_root=tmp_path / "review",
    )
    return verifier.review(result or run.result, source)


def test_genuine_candidate_passes_and_converts_to_an_atlas_review(
    tmp_path: Path, candidate: E2ERun
) -> None:
    verdict = _review(tmp_path, candidate)
    assert verdict.verdict is ReviewVerdict.PASSED
    assert verdict.reviewer_agent_id == SENTINELQA_AGENT_ID
    assert verdict.review_id == REVIEW_ID
    store = LocalArtifactStore(tmp_path / "verdicts")
    evidence = store.put(verdict.model_dump_json().encode("utf-8"), artifact_type="x")
    with pytest.raises(ValueError, match="does not reference this verdict"):
        verdict.to_atlas_review(evidence)
    from nexus.patchforge.canonical import canonical_json

    evidence = store.put(
        canonical_json(verdict).encode("utf-8"), artifact_type="sentinelqa_verdict"
    )
    review = verdict.to_atlas_review(evidence)
    assert review.verdict is ReviewVerdict.PASSED
    assert review.evidence == [evidence]
    assert review.reviewer_agent_id == "sentinelqa.reviewer"


def test_forged_patch_touching_a_protected_path_is_rejected(
    tmp_path: Path, candidate: E2ERun
) -> None:
    git = GitRunner(tmp_path / "git-forge")

    def mutate(tree: Path) -> None:
        (tree / "calculator.py").write_text(FIXED, encoding="utf-8")
        (tree / "protected.txt").write_text("rewritten\n", encoding="utf-8")

    patch = _git_patch(git, candidate.root / "source", mutate)
    forged = _forge(candidate, patch, ["calculator.py", "protected.txt"])
    verdict = _review(tmp_path, candidate, forged)
    assert verdict.verdict is ReviewVerdict.FAILED
    codes = {item.code for item in verdict.findings}
    assert SentinelFindingCode.PROTECTED_PATH_CHANGED in codes


def test_unrecorded_change_in_the_patch_is_rejected(tmp_path: Path, candidate: E2ERun) -> None:
    git = GitRunner(tmp_path / "git-forge")

    def mutate(tree: Path) -> None:
        (tree / "calculator.py").write_text(FIXED, encoding="utf-8")
        (tree / "helper.py").write_text("hidden = True\n", encoding="utf-8")

    patch = _git_patch(git, candidate.root / "source", mutate)
    forged = _forge(candidate, patch, ["calculator.py"])
    verdict = _review(tmp_path, candidate, forged)
    assert verdict.verdict is ReviewVerdict.FAILED
    finding = next(
        item for item in verdict.findings if item.code is SentinelFindingCode.UNRECORDED_CHANGE
    )
    assert finding.path == "helper.py"


def test_recorded_change_absent_from_the_patch_is_unverifiable(
    tmp_path: Path, candidate: E2ERun
) -> None:
    diff = candidate.result.diff
    assert diff is not None
    forged = candidate.result.model_copy(
        update={"diff": diff.model_copy(update={"changed_files": ["calculator.py", "ghost.py"]})}
    )
    verdict = _review(tmp_path, candidate, forged)
    assert verdict.verdict is ReviewVerdict.INCONCLUSIVE
    finding = next(
        item for item in verdict.findings if item.code is SentinelFindingCode.EVIDENCE_INCOMPLETE
    )
    assert finding.path == "ghost.py"
    assert verdict.runs and all(item.tree is SpecificationTree.PRISTINE for item in verdict.runs)


def test_symlink_replacing_a_test_is_rejected(tmp_path: Path, candidate: E2ERun) -> None:
    git = GitRunner(tmp_path / "git-forge")

    def mutate(tree: Path) -> None:
        (tree / "calculator.py").write_text(FIXED, encoding="utf-8")
        target = tree / "tests" / "test_calculator.py"
        target.unlink()
        os.symlink("../calculator.py", target)

    patch = _git_patch(git, candidate.root / "source", mutate)
    forged = _forge(candidate, patch, ["calculator.py", "tests/test_calculator.py"])
    verdict = _review(tmp_path, candidate, forged)
    assert verdict.verdict is ReviewVerdict.FAILED
    codes = {item.code for item in verdict.findings}
    assert SentinelFindingCode.SPECIFICATION_NOT_REGULAR_FILE in codes
    assert verdict.integrity is not None
    assert verdict.integrity.not_regular == ["tests/test_calculator.py"]
    # The verification tree restored the real test and ran it against the fixed code.
    assert verdict.integrity.restored == ["tests/test_calculator.py"]
    assert verdict.integrity.verification_changed_files == ["calculator.py"]


def test_patch_that_does_not_apply_is_rejected(tmp_path: Path, candidate: E2ERun) -> None:
    diff = candidate.result.diff
    assert diff is not None
    original = candidate.artifacts.read(diff.patch_artifact)
    broken = original.replace(b"-    return a + b\n", b"-    return a * b\n")
    forged = _forge(candidate, broken, ["calculator.py"])
    verdict = _review(tmp_path, candidate, forged)
    assert verdict.verdict is ReviewVerdict.FAILED
    assert verdict.finding_codes == ["patch_apply_failed"]


class _LyingStore:
    """An artifact store that returns bytes other than the attested patch."""

    def __init__(self, content: bytes) -> None:
        self.content = content

    def read(self, reference: ArtifactReference) -> bytes:
        return self.content


def test_artifact_bytes_that_do_not_match_the_attested_hash_are_unverifiable(
    tmp_path: Path, candidate: E2ERun
) -> None:
    diff = candidate.result.diff
    assert diff is not None
    # A consistent but different artifact is judged on its own merits: it does not apply.
    other = candidate.artifacts.put(b"--- other\n", artifact_type=PATCH_ARTIFACT_TYPE)
    mismatched = diff.model_copy(update={"patch_artifact": other, "diff_sha256": other.sha256})
    forged = candidate.result.model_copy(update={"diff": mismatched})
    assert _review(tmp_path / "a", candidate, forged).verdict is ReviewVerdict.FAILED
    # A store that hands back other bytes for the attested reference is not evidence.
    verdict = _review(
        tmp_path / "b",
        candidate,
        artifacts=_LyingStore(candidate.artifacts.read(diff.patch_artifact) + b"\n# extra\n"),
    )
    assert verdict.verdict is ReviewVerdict.INCONCLUSIVE
    assert verdict.finding_codes == ["evidence_incomplete"]
    assert verdict.runs == []
    empty = _review(tmp_path / "c", candidate, artifacts=_LyingStore(b""))
    assert empty.verdict is ReviewVerdict.INCONCLUSIVE


def test_task_binding_mismatch_is_unverifiable(tmp_path: Path, candidate: E2ERun) -> None:
    identity = candidate.result.identity.model_copy(update={"task_sha256": "0" * 64})
    forged = candidate.result.model_copy(update={"identity": identity})
    verdict = _review(tmp_path, candidate, forged)
    assert verdict.verdict is ReviewVerdict.INCONCLUSIVE
    assert verdict.finding_codes == ["identity_mismatch"]
    assert verdict.runs == []


def test_verdict_contract_refuses_a_pass_without_evidence(
    tmp_path: Path, candidate: E2ERun
) -> None:
    verdict = _review(tmp_path, candidate)
    with pytest.raises(ValueError, match="passing pristine targeted and full runs"):
        verdict.model_copy(update={"runs": []}).model_validate(
            verdict.model_copy(update={"runs": []}).model_dump()
        )
    with pytest.raises(ValueError, match="does not follow from its findings"):
        SentinelVerdict.model_validate({**verdict.model_dump(), "verdict": ReviewVerdict.FAILED})
    digest = sha256(b"x").hexdigest()
    assert len(digest) == 64


def test_reproduction_commands_need_no_pytest_summary(tmp_path: Path, candidate: E2ERun) -> None:
    """Reproduction may be any operator check (for example a formatter); only the targeted
    and full-suite runs must report pytest counts."""

    from nexus.patchforge.policy import CommandPurpose
    from nexus.patchforge.sandbox import SandboxStatus
    from nexus.sentinelqa.catalog import baseline_plans, verification_plans
    from nexus.sentinelqa.harness import spec_plan

    profile = candidate.scenario.profile
    plans = [
        spec_plan(
            profile,
            CommandPurpose.REPRODUCTION,
            SandboxStatus.FAILED,
            summary="Would reformat: calculator.py\n1 file would be reformatted",
        ),
        *baseline_plans(profile)[1:],
        *verification_plans(profile),
    ]
    verdict = _review(tmp_path, candidate, sandbox=FakeSandbox(plans, clock=lambda: NOW))
    assert verdict.verdict is ReviewVerdict.PASSED
    assert verdict.runs[0].counts is None and verdict.runs[1].counts is not None
