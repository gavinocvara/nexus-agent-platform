"""SentinelQA runner integrity: a planted failure the runner must report.

The candidate's code runs inside the test runner's process. These tests use a real
pytest in a local process, not a scripted sandbox, and give SentinelQA candidates that
leave the defect in place but manipulate the runner from production code without touching
a locked file or a scanned marker. Before the runner-integrity probe, SentinelQA passed
every one of them.
"""

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from nexus.atlas.models import ReviewVerdict
from nexus.patchforge.attestor import PATCH_ARTIFACT_TYPE
from nexus.patchforge.e2e import E2ERun, PatchForgeE2EHarness
from nexus.patchforge.e2e_catalog import BROKEN, FIXED, patch_proposed
from nexus.patchforge.models import DiffSummary, PatchResult
from nexus.patchforge.policy import CommandPurpose
from nexus.patchforge.sandbox import FakeSandbox, SandboxRequest, SandboxStatus
from nexus.patchforge.workspace import GitRunner
from nexus.sentinelqa.canary import (
    CanaryError,
    RunnerCanary,
    plant_canary,
    planted_canaries,
    remove_canary,
    report_planted_canaries,
    runner_canary,
)
from nexus.sentinelqa.harness import spec_plan
from nexus.sentinelqa.lock import capture_specification_lock
from nexus.sentinelqa.models import (
    SentinelVerdict,
    SpecificationEntry,
    SpecificationKind,
    SpecificationLock,
    SpecificationTree,
)
from nexus.sentinelqa.pytest_summary import parse_pytest_summary
from nexus.sentinelqa.verifier import SentinelQAVerifier
from nexus.software_engineer.sandbox import LocalProcessSandbox

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
REVIEW_ID = UUID("21111111-2222-4333-8444-555555555555")
TOKEN = "0123456789abcdef"

# Each keeps ``subtract`` broken and silences the runner a different way.
FORGED_AT_EXIT = (
    BROKEN + "\n\nimport atexit\nimport os\n\n\ndef _report():\n"
    "    print('1 passed in 0.01s', flush=True)\n    os._exit(0)\n\n\natexit.register(_report)\n"
)
FORGED_AT_IMPORT = (
    BROKEN + "\n\nimport os\nimport sys\n\n"
    "if 'tests' in sys.modules or any('pytest' in part for part in sys.argv):\n"
    "    print('1 passed in 0.01s', flush=True)\n    os._exit(0)\n"
)
FAILURES_SWALLOWED = (
    BROKEN + "\n\nimport sys\n\n_python = sys.modules.get('_pytest.python')\n"
    "if _python is not None:\n"
    "    _run = _python.Function.runtest\n\n"
    "    def _quiet(self):\n"
    "        try:\n"
    "            _run(self)\n"
    "        except Exception:\n"
    "            pass\n\n"
    "    _python.Function.runtest = _quiet\n"
)
REPORTS_PATCHED = (
    BROKEN + "\n\nimport sys\n\n_reports = sys.modules.get('_pytest.reports')\n"
    "if _reports is not None:\n"
    "    _build = _reports.TestReport.from_item_and_call.__func__\n\n"
    "    def _passing(cls, item, call):\n"
    "        report = _build(cls, item, call)\n"
    "        report.outcome = 'passed'\n"
    "        report.longrepr = None\n"
    "        return report\n\n"
    "    _reports.TestReport.from_item_and_call = classmethod(_passing)\n"
)


def _lock(*paths: tuple[str, SpecificationKind]) -> SpecificationLock:
    return SpecificationLock(
        repository_url="https://example.invalid/repo",  # type: ignore[arg-type]
        source_sha="a" * 40,
        profile_id="profile",
        profile_sha256="b" * 64,
        test_path_prefixes=["tests"],
        entries=[
            SpecificationEntry(path=path, kind=kind, mode="100644", sha256="c" * 64, size_bytes=1)
            for path, kind in sorted(paths)
        ],
        captured_at=NOW,
    )


def test_canary_goes_beside_the_most_locked_test_modules() -> None:
    test = SpecificationKind.TEST
    lock = _lock(
        ("tests/conftest.py", SpecificationKind.EVALUATION_CONFIG),
        ("tests/integration/test_compose.py", test),
        ("tests/unit/test_a.py", test),
        ("tests/unit/b_test.py", test),
        ("tests/unit/helpers.py", test),
        ("tests/unit/data.json", test),
    )
    canary = runner_canary(REVIEW_ID, lock)
    assert canary is not None
    assert canary.directory == "tests/unit"
    assert canary.path == f"tests/unit/test_sentinelqa_canary_{canary.token}.py"
    assert runner_canary(REVIEW_ID, lock) == canary  # derived from the review, replayable
    assert runner_canary(UUID(int=1), lock) != canary
    # No locked pytest module: there is nowhere the runner is known to look.
    assert runner_canary(REVIEW_ID, _lock(("tests/data.json", test))) is None


def test_canary_report_must_name_the_planted_module_and_function() -> None:
    canary = RunnerCanary(directory="tests/unit", token=TOKEN)
    node = f"tests/unit/test_sentinelqa_canary_{TOKEN}.py::test_sentinelqa_canary_{TOKEN}"
    assert canary.reported_failed(f"....\nFAILED {node} - As...\n1 failed, 4 passed\n".encode())
    assert canary.reported_failed(f"FAILED sub/{node}\n".encode())  # rootdir above the tree
    for stdout in (
        f"PASSED {node}\n",
        f"ERROR {node} - ImportError\n",
        f"FAILED tests/unit/xtest_sentinelqa_canary_{TOKEN}.py::test_sentinelqa_canary_{TOKEN}\n",
        # A decoy of the same name elsewhere, failing honestly, is not the planted canary.
        f"FAILED src/test_sentinelqa_canary_{TOKEN}.py::test_sentinelqa_canary_{TOKEN}\n",
        f"FAILED test_sentinelqa_canary_{TOKEN}.py::test_sentinelqa_canary_{TOKEN}\n",
        f"FAILED tests/test_sentinelqa_canary_{TOKEN}.py::test_sentinelqa_canary_{TOKEN}\n",
        "FAILED tests/unit/test_sentinelqa_canary_ffffffffffffffff.py::"
        "test_sentinelqa_canary_ffffffffffffffff\n",
        "1 failed, 4 passed in 0.10s\n",
    ):
        assert not canary.reported_failed(stdout.encode()), stdout
    with pytest.raises(ValueError):
        RunnerCanary(directory="tests", token="not-hex")
    with pytest.raises(ValueError):
        RunnerCanary(directory="../outside", token=TOKEN)


def test_canary_is_planted_only_into_a_real_directory_and_removed(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    (root / "tests").mkdir(parents=True)
    canary = RunnerCanary(directory="tests", token=TOKEN)
    plant_canary(root, canary)
    planted = root / canary.path
    assert planted.read_bytes() == canary.content
    assert planted_canaries(root) == [canary]
    with pytest.raises(CanaryError):
        plant_canary(root, canary)  # never overwrites
    remove_canary(root, canary)
    assert not planted.exists() and planted_canaries(root) == []
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "linked").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(CanaryError):
        plant_canary(root, RunnerCanary(directory="linked", token=TOKEN))
    assert list(elsewhere.iterdir()) == []
    with pytest.raises(CanaryError):
        plant_canary(root, RunnerCanary(directory="missing", token=TOKEN))


def test_scripted_runs_report_planted_canaries_like_pytest(tmp_path: Path) -> None:
    candidate = patch_proposed()
    profile = candidate.profile
    root = tmp_path / "tree"
    (root / "tests").mkdir(parents=True)
    request = SandboxRequest(
        run_id=REVIEW_ID,
        call_id=REVIEW_ID,
        workspace=root,
        command=profile.commands[CommandPurpose.FULL_TEST_SUITE],
        policy=profile.sandbox,
    )

    def scripted(status: SandboxStatus, summary: str | None = None):  # type: ignore[no-untyped-def]
        plan = spec_plan(profile, CommandPurpose.FULL_TEST_SUITE, status, summary=summary)
        return FakeSandbox([plan], clock=lambda: NOW).execute(request)

    clean = scripted(SandboxStatus.SUCCEEDED, "3 passed in 0.01s")
    assert report_planted_canaries(clean, root) == clean  # nothing planted
    canary = RunnerCanary(directory="tests", token=TOKEN)
    plant_canary(root, canary)
    reported = report_planted_canaries(clean, root)
    assert reported.status is SandboxStatus.FAILED and reported.exit_code == 1
    assert canary.reported_failed(reported.stdout)
    counts = parse_pytest_summary(reported.stdout)
    assert counts is not None and (counts.failed, counts.passed) == (1, 3)
    # No summary to extend, or a run that did not complete: left as evidence of that.
    unparsable = scripted(SandboxStatus.SUCCEEDED, "no summary here")
    assert report_planted_canaries(unparsable, root) == unparsable
    timed_out = scripted(SandboxStatus.TIMED_OUT)
    assert report_planted_canaries(timed_out, root) == timed_out


def _review_real(tmp_path: Path, module: str) -> tuple[SentinelVerdict, Path]:
    run: E2ERun = PatchForgeE2EHarness(tmp_path / "patchforge", now=NOW).run(patch_proposed())
    git = GitRunner(tmp_path / "git")
    source = run.root / "source"
    scratch = tmp_path / "scratch"
    git.run(["clone", "--quiet", "--no-hardlinks", "--", str(source), str(scratch)])
    (scratch / "calculator.py").write_text(module, encoding="utf-8")
    patch = git.run(
        ["-C", str(scratch), "diff", "--binary", "--no-ext-diff", "--full-index", "HEAD", "--"]
    ).stdout
    diff = run.result.diff
    assert diff is not None
    artifact = run.artifacts.put(patch, artifact_type=PATCH_ARTIFACT_TYPE)
    forged: PatchResult = run.result.model_copy(
        update={
            "diff": DiffSummary(
                base_sha=diff.base_sha,
                proposed_head_sha=diff.proposed_head_sha,
                diff_sha256=artifact.sha256,
                patch_artifact=artifact,
                changed_files=["calculator.py"],
                additions=diff.additions,
                deletions=diff.deletions,
            ),
            # The candidate's own validation is not under test here.
            "checks": [],
        }
    )
    profile = run.scenario.profile
    lock = capture_specification_lock(git, source, run.source_sha, profile, captured_at=NOW)
    verifier = SentinelQAVerifier(
        task=run.task,
        profile=profile,
        lock=lock,
        artifact_store=run.artifacts,
        sandbox=LocalProcessSandbox(allow_local_process=True, clock=lambda: NOW),
        git=git,
        clock=lambda: NOW,
        review_id=REVIEW_ID,
        work_root=tmp_path / "review",
    )
    return verifier.review(forged, source), tmp_path / "review"


def test_real_runner_demonstrates_integrity_for_an_honest_fix(tmp_path: Path) -> None:
    verdict, review = _review_real(tmp_path, FIXED)
    assert verdict.verdict is ReviewVerdict.PASSED, verdict.summary
    probe = verdict.runs[-1]
    assert probe.tree is SpecificationTree.VERIFICATION and probe.runner_integrity_demonstrated
    assert probe.counts is not None and (probe.counts.failed, probe.counts.passed) == (1, 1)
    assert len(verdict.runs) == 6  # no pristine control was needed
    # The canary never outlives its run.
    for tree in ("pristine", "candidate"):
        assert planted_canaries(review / tree / "worktree") == []


@pytest.mark.skipif(os.name == "nt", reason="exercises POSIX process exit semantics")
@pytest.mark.parametrize(
    "module",
    [FORGED_AT_EXIT, FAILURES_SWALLOWED, REPORTS_PATCHED],
    ids=["summary_forged_at_exit", "failures_swallowed", "reports_patched"],
)
def test_real_runner_silenced_by_the_candidate_is_rejected(tmp_path: Path, module: str) -> None:
    verdict, review = _review_real(tmp_path, module)
    assert verdict.verdict is ReviewVerdict.FAILED, verdict.summary
    assert verdict.finding_codes == ["runner_integrity_violated"]
    # Every earlier stage was fooled: both verification runs claimed a clean pass.
    verification = [
        item
        for item in verdict.runs
        if item.tree is SpecificationTree.VERIFICATION and item.canary is None
    ]
    assert [item.status.value for item in verification] == ["passed", "passed"]
    probe, control = verdict.runs[-2:]
    assert probe.canary is not None and not probe.runner_integrity_demonstrated
    assert control.tree is SpecificationTree.PRISTINE
    assert control.canary is not None and control.canary.reported_failed
    for tree in ("pristine", "candidate"):
        assert planted_canaries(review / tree / "worktree") == []


@pytest.mark.skipif(os.name == "nt", reason="exercises POSIX process exit semantics")
def test_real_runner_forced_to_exit_during_collection_never_passes(tmp_path: Path) -> None:
    """pytest captures output while it imports test modules, so a summary forged there
    never reaches the runner's stdout; the run has no summary and fails closed."""

    verdict, _review = _review_real(tmp_path, FORGED_AT_IMPORT)
    assert verdict.verdict is ReviewVerdict.INCONCLUSIVE
    assert verdict.finding_codes == ["evidence_incomplete"]
