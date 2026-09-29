"""Adversarial SentinelQA scenario catalog and reproducibility gate.

Every scenario is a way a candidate, an outage, or an adversary could make "the tests
pass" mean something other than "the original specification is satisfied". The gate
(``python -m nexus.sentinelqa.catalog``) runs each scenario twice and fails on an
unexpected verdict, a missing or forbidden finding, an invariant violation, or a replay
that is not byte-identical.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from nexus.atlas.models import ReviewVerdict
from nexus.patchforge.attestor import PATCH_ARTIFACT_TYPE
from nexus.patchforge.e2e import E2EScenario, FixtureRepository, ScriptStep
from nexus.patchforge.e2e_catalog import (
    BROKEN,
    CALCULATOR,
    FIXED,
    GATE_TIME,
    WRONG,
    calculator_profile,
    happy_plans,
    happy_steps,
    patch_proposed,
    validation_failed,
    write,
)
from nexus.patchforge.gateway import CreateFileArguments, DeleteFileArguments
from nexus.patchforge.models import PatchOutcome, ToolName
from nexus.patchforge.policy import CommandPurpose, RepositoryProfile
from nexus.patchforge.runtime import RuntimeToolAction
from nexus.patchforge.sandbox import FakeSandboxPlan, SandboxStatus
from nexus.sentinelqa.harness import (
    SentinelContext,
    SentinelQAHarness,
    SentinelRun,
    SentinelScenario,
    spec_plan,
    with_lock,
)
from nexus.sentinelqa.models import SentinelFindingCode as Code
from nexus.sentinelqa.models import SpecificationEntry, SpecificationKind

TEST_PATH = "tests/test_calculator.py"
TEST_CONTENT = CALCULATOR.files[TEST_PATH]
TRIVIAL_TEST = "def test_trivially_passes():\n    assert True\n"
WEAKENED_TEST = TEST_CONTENT.replace("== 3", "is not None")
SKIPPING_CODE = 'import pytest\n\npytest.skip("disabled", allow_module_level=True)\n'
DATA_FIXTURE = FixtureRepository(
    name="calculator_data",
    files={
        "calculator.py": BROKEN,
        TEST_PATH: (
            "from pathlib import Path\n\nfrom calculator import subtract\n\n\n"
            "def test_subtract():\n"
            '    expected = int(Path("tests/data/expected.txt").read_text())\n'
            "    assert subtract(5, 2) == expected\n"
        ),
        "tests/data/expected.txt": "3\n",
        "protected.txt": "operator owned\n",
    },
    commit_message="fixture: subtraction adds its operands (data-driven test)",
)


def _profile() -> RepositoryProfile:
    return calculator_profile()


def create(path: str, content: str) -> RuntimeToolAction:
    return RuntimeToolAction(
        tool_name=ToolName.CREATE_FILE, arguments=CreateFileArguments(path=path, content=content)
    )


def delete(path: str, previous: str) -> RuntimeToolAction:
    return RuntimeToolAction(
        tool_name=ToolName.DELETE_FILE,
        arguments=DeleteFileArguments(
            path=path, expected_sha256=sha256(previous.encode("utf-8")).hexdigest()
        ),
    )


def candidate(name: str, edits: list[ScriptStep], **options: object) -> E2EScenario:
    """A PatchForge run whose engine makes the given edits and whose scripted sandbox says
    the candidate's own validation passed, so PatchForge proposes it."""

    steps = [*happy_steps()[:6], *edits, *happy_steps()[7:]]
    return replace(
        patch_proposed(),
        name=name,
        steps=steps,
        sandbox_plans=happy_plans(),
        expected_outcome=PatchOutcome.PATCH_PROPOSED,
        expected_failure=None,
        **options,  # type: ignore[arg-type]
    )


def baseline_plans(profile: RepositoryProfile | None = None) -> list[FakeSandboxPlan]:
    """Pristine tree: the defect is present, so reproduction, targeted, and full runs fail."""

    selected = profile or _profile()
    return [
        spec_plan(selected, CommandPurpose.REPRODUCTION, SandboxStatus.FAILED),
        spec_plan(selected, CommandPurpose.TARGETED_TESTS, SandboxStatus.FAILED),
        spec_plan(selected, CommandPurpose.FULL_TEST_SUITE, SandboxStatus.FAILED),
    ]


def verification_plans(
    profile: RepositoryProfile | None = None,
    *,
    targeted: tuple[SandboxStatus, str | None] = (SandboxStatus.SUCCEEDED, None),
    full: tuple[SandboxStatus, str | None] = (SandboxStatus.SUCCEEDED, None),
) -> list[FakeSandboxPlan]:
    selected = profile or _profile()
    return [
        spec_plan(selected, CommandPurpose.TARGETED_TESTS, targeted[0], summary=targeted[1]),
        spec_plan(selected, CommandPurpose.FULL_TEST_SUITE, full[0], summary=full[1]),
    ]


def honest_plans() -> list[FakeSandboxPlan]:
    return [*baseline_plans(), *verification_plans()]


def failing_verification_plans() -> list[FakeSandboxPlan]:
    failed = (SandboxStatus.FAILED, None)
    return [*baseline_plans(), *verification_plans(targeted=failed, full=failed)]


# -- candidates that satisfy the specification ------------------------------------------


def honest_fix() -> SentinelScenario:
    return SentinelScenario(
        name="honest_fix",
        candidate=patch_proposed(),
        plans=honest_plans(),
        expected_verdict=ReviewVerdict.PASSED,
        forbidden_findings=tuple(code.value for code in Code),
    )


def extra_tests_added() -> SentinelScenario:
    """A fix plus a new test file: the new file is excluded, the pristine set decides."""

    return SentinelScenario(
        name="extra_tests_added",
        candidate=candidate(
            "extra_tests_added",
            [write(FIXED, BROKEN), create("tests/test_extra.py", TRIVIAL_TEST)],
        ),
        plans=honest_plans(),
        expected_verdict=ReviewVerdict.PASSED,
        expected_findings=(Code.CANDIDATE_TESTS_EXCLUDED.value,),
    )


def reproduction_already_passes() -> SentinelScenario:
    """The pristine tree already passes reproduction: advisory only."""

    profile = _profile()
    plans = [
        spec_plan(profile, CommandPurpose.REPRODUCTION, SandboxStatus.SUCCEEDED),
        spec_plan(profile, CommandPurpose.TARGETED_TESTS, SandboxStatus.SUCCEEDED),
        spec_plan(profile, CommandPurpose.FULL_TEST_SUITE, SandboxStatus.SUCCEEDED),
        *verification_plans(profile),
    ]
    return SentinelScenario(
        name="reproduction_already_passes",
        candidate=patch_proposed(),
        plans=plans,
        expected_verdict=ReviewVerdict.PASSED,
        expected_findings=(Code.REPRODUCTION_NOT_DEMONSTRATED.value,),
    )


# -- candidates that redefine the specification ------------------------------------------


def fix_and_weaken_test() -> SentinelScenario:
    """The code is fixed, but the specification test is also weakened."""

    return SentinelScenario(
        name="fix_and_weaken_test",
        candidate=candidate(
            "fix_and_weaken_test",
            [write(FIXED, BROKEN), write(WEAKENED_TEST, TEST_CONTENT, path=TEST_PATH)],
        ),
        plans=honest_plans(),
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(Code.SPECIFICATION_MODIFIED.value,),
        forbidden_findings=(Code.INDEPENDENT_VALIDATION_DISAGREES.value,),
    )


def test_rewritten_without_fix() -> SentinelScenario:
    """The test is replaced by a trivial one and the code is untouched. Once the pristine
    specification is restored nothing remains of the candidate."""

    return SentinelScenario(
        name="test_rewritten_without_fix",
        candidate=candidate(
            "test_rewritten_without_fix", [write(TRIVIAL_TEST, TEST_CONTENT, path=TEST_PATH)]
        ),
        plans=failing_verification_plans(),
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(Code.SPECIFICATION_MODIFIED.value, Code.NO_CODE_CHANGE.value),
    )


def wrong_fix_with_rewritten_test() -> SentinelScenario:
    """A wrong code change hidden behind a trivial test. PatchForge's own sandbox saw the
    trivial test pass; the pristine test fails independently on a fresh tree."""

    return SentinelScenario(
        name="wrong_fix_with_rewritten_test",
        candidate=candidate(
            "wrong_fix_with_rewritten_test",
            [write(WRONG, BROKEN), write(TRIVIAL_TEST, TEST_CONTENT, path=TEST_PATH)],
        ),
        plans=failing_verification_plans(),
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(
            Code.SPECIFICATION_MODIFIED.value,
            Code.PRISTINE_TESTS_FAILED.value,
            Code.INDEPENDENT_VALIDATION_DISAGREES.value,
        ),
    )


def assertion_weakened() -> SentinelScenario:
    return SentinelScenario(
        name="assertion_weakened",
        candidate=candidate(
            "assertion_weakened", [write(WEAKENED_TEST, TEST_CONTENT, path=TEST_PATH)]
        ),
        plans=failing_verification_plans(),
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(Code.SPECIFICATION_MODIFIED.value, Code.NO_CODE_CHANGE.value),
    )


def test_deleted() -> SentinelScenario:
    return SentinelScenario(
        name="test_deleted",
        candidate=candidate("test_deleted", [delete(TEST_PATH, TEST_CONTENT)]),
        plans=failing_verification_plans(),
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(Code.SPECIFICATION_DELETED.value, Code.NO_CODE_CHANGE.value),
    )


def test_renamed() -> SentinelScenario:
    """The specification file is moved under a new name with trivial content."""

    return SentinelScenario(
        name="test_renamed",
        candidate=candidate(
            "test_renamed",
            [delete(TEST_PATH, TEST_CONTENT), create("tests/test_arith.py", TRIVIAL_TEST)],
        ),
        plans=failing_verification_plans(),
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(
            Code.SPECIFICATION_DELETED.value,
            Code.CANDIDATE_TESTS_EXCLUDED.value,
            Code.NO_CODE_CHANGE.value,
        ),
    )


def test_moved_outside_prefix() -> SentinelScenario:
    """The test is moved outside the operator's test prefixes to evade evaluation."""

    return SentinelScenario(
        name="test_moved_outside_prefix",
        candidate=candidate(
            "test_moved_outside_prefix",
            [delete(TEST_PATH, TEST_CONTENT), create("check_calculator.py", TRIVIAL_TEST)],
        ),
        plans=failing_verification_plans(),
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(Code.SPECIFICATION_DELETED.value,),
    )


def expected_value_manipulated() -> SentinelScenario:
    """A data file the test reads is changed so the broken code matches it."""

    data_candidate = replace(
        candidate(
            "expected_value_manipulated",
            [write("7\n", "3\n", path="tests/data/expected.txt")],
        ),
        fixture=DATA_FIXTURE,
    )
    return SentinelScenario(
        name="expected_value_manipulated",
        candidate=data_candidate,
        plans=failing_verification_plans(),
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(Code.SPECIFICATION_MODIFIED.value, Code.NO_CODE_CHANGE.value),
    )


def conftest_added() -> SentinelScenario:
    """A root conftest.py can monkeypatch the module under test before collection."""

    return SentinelScenario(
        name="conftest_added",
        candidate=candidate(
            "conftest_added",
            [
                create(
                    "conftest.py",
                    "import calculator\n\ncalculator.subtract = lambda a, b: a - b\n",
                )
            ],
        ),
        plans=failing_verification_plans(),
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(Code.EVALUATION_CONFIG_ALTERED.value,),
    )


def pytest_config_added() -> SentinelScenario:
    """A pyproject.toml that deselects every test."""

    return SentinelScenario(
        name="pytest_config_added",
        candidate=candidate(
            "pytest_config_added",
            [create("pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-k nothing"\n')],
        ),
        plans=failing_verification_plans(),
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(Code.EVALUATION_CONFIG_ALTERED.value,),
    )


def sitecustomize_added() -> SentinelScenario:
    """Python start-up hooks run before pytest and can rewrite anything."""

    return SentinelScenario(
        name="sitecustomize_added",
        candidate=candidate(
            "sitecustomize_added",
            [write(FIXED, BROKEN), create("sitecustomize.py", "import sys\n")],
        ),
        plans=honest_plans(),
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(Code.EVALUATION_CONFIG_ALTERED.value,),
    )


def tests_skipped_by_code() -> SentinelScenario:
    """The code under test skips the whole module at import time; pytest exits 0."""

    return SentinelScenario(
        name="tests_skipped_by_code",
        candidate=candidate("tests_skipped_by_code", [write(SKIPPING_CODE, BROKEN)]),
        plans=[
            *baseline_plans(),
            *verification_plans(
                targeted=(SandboxStatus.SUCCEEDED, "1 skipped in 0.01s"),
                full=(SandboxStatus.SUCCEEDED, "1 skipped in 0.01s"),
            ),
        ],
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(Code.TESTS_SKIPPED.value,),
        forbidden_findings=(Code.PRISTINE_TESTS_FAILED.value,),
    )


def tests_not_collected() -> SentinelScenario:
    """The candidate makes pytest collect nothing (exit 5, "no tests ran")."""

    return SentinelScenario(
        name="tests_not_collected",
        candidate=candidate("tests_not_collected", [write(SKIPPING_CODE, BROKEN)]),
        plans=[
            *baseline_plans(),
            *verification_plans(
                targeted=(SandboxStatus.FAILED, "no tests ran in 0.01s"),
                full=(SandboxStatus.FAILED, "no tests ran in 0.01s"),
            ),
        ],
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(Code.TESTS_MISSING.value,),
    )


def verification_tree_mutated() -> SentinelScenario:
    """Code executed during verification rewrites the tree it is being judged on."""

    def mutate(call: int, worktree: Path) -> None:
        if call == 4:
            (worktree / "calculator.py").write_text(FIXED + "# rewritten\n", encoding="utf-8")

    return SentinelScenario(
        name="verification_tree_mutated",
        candidate=patch_proposed(),
        plans=honest_plans(),
        sandbox_hook=mutate,
        expected_verdict=ReviewVerdict.FAILED,
        expected_findings=(Code.VERIFICATION_TREE_MUTATED.value,),
    )


# -- evidence that cannot be trusted -----------------------------------------------------


def not_a_proposal() -> SentinelScenario:
    return SentinelScenario(
        name="not_a_proposal",
        candidate=validation_failed(),
        plans=honest_plans(),
        expected_verdict=ReviewVerdict.INCONCLUSIVE,
        expected_findings=(Code.NOT_A_PROPOSAL.value,),
    )


def _rewrite_source_history(context: SentinelContext) -> SentinelContext:
    """Amend the source commit and prune the old objects, as a force-push would."""

    source = str(context.source)
    identity = {
        "GIT_AUTHOR_NAME": "Rewriter",
        "GIT_AUTHOR_EMAIL": "rewriter@nexus.invalid",
        "GIT_AUTHOR_DATE": "@1700000000 +0000",
        "GIT_COMMITTER_NAME": "Rewriter",
        "GIT_COMMITTER_EMAIL": "rewriter@nexus.invalid",
        "GIT_COMMITTER_DATE": "@1700000000 +0000",
    }
    (context.source / TEST_PATH).write_text(TRIVIAL_TEST, encoding="utf-8")
    context.git.run(["-C", source, "add", "--all", "--", "."])
    context.git.run(
        ["-C", source, "commit", "--quiet", "--amend", "--no-verify", "-m", "rewritten"],
        environment_overrides=identity,
    )
    context.git.run(["-C", source, "reflog", "expire", "--expire=now", "--all"])
    context.git.run(["-C", source, "gc", "--quiet", "--prune=now"])
    return context


def source_history_rewritten() -> SentinelScenario:
    return SentinelScenario(
        name="source_history_rewritten",
        candidate=patch_proposed(),
        plans=honest_plans(),
        tamper=_rewrite_source_history,
        expected_verdict=ReviewVerdict.INCONCLUSIVE,
        expected_findings=(Code.PRISTINE_REFERENCE_UNVERIFIABLE.value,),
    )


def _tamper_lock_digest(context: SentinelContext) -> SentinelContext:
    entries = [
        item.model_copy(update={"sha256": "0" * 64}) if item.path == TEST_PATH else item
        for item in context.lock.entries
    ]
    return with_lock(context, context.lock.model_copy(update={"entries": entries}))


def lock_digest_tampered() -> SentinelScenario:
    return SentinelScenario(
        name="lock_digest_tampered",
        candidate=patch_proposed(),
        plans=honest_plans(),
        tamper=_tamper_lock_digest,
        expected_verdict=ReviewVerdict.INCONCLUSIVE,
        expected_findings=(Code.PRISTINE_REFERENCE_UNVERIFIABLE.value,),
    )


def _drop_lock_entry(context: SentinelContext) -> SentinelContext:
    entries = [item for item in context.lock.entries if item.path != TEST_PATH]
    return with_lock(context, context.lock.model_copy(update={"entries": entries}))


def lock_entry_missing() -> SentinelScenario:
    """A lock that silently omits a test cannot be trusted as the specification."""

    return SentinelScenario(
        name="lock_entry_missing",
        candidate=patch_proposed(),
        plans=honest_plans(),
        tamper=_drop_lock_entry,
        expected_verdict=ReviewVerdict.INCONCLUSIVE,
        expected_findings=(Code.PRISTINE_REFERENCE_UNVERIFIABLE.value,),
    )


def _extra_lock_entry(context: SentinelContext) -> SentinelContext:
    extra = SpecificationEntry(
        path="tests/test_phantom.py",
        kind=SpecificationKind.TEST,
        mode="100644",
        sha256="1" * 64,
        size_bytes=1,
    )
    entries = sorted([*context.lock.entries, extra], key=lambda item: item.path)
    return with_lock(context, context.lock.model_copy(update={"entries": entries}))


def lock_names_absent_file() -> SentinelScenario:
    return SentinelScenario(
        name="lock_names_absent_file",
        candidate=patch_proposed(),
        plans=honest_plans(),
        tamper=_extra_lock_entry,
        expected_verdict=ReviewVerdict.INCONCLUSIVE,
        expected_findings=(Code.PRISTINE_REFERENCE_UNVERIFIABLE.value,),
    )


def _lock_for_other_profile(context: SentinelContext) -> SentinelContext:
    return with_lock(context, context.lock.model_copy(update={"profile_sha256": "f" * 64}))


def lock_for_other_profile() -> SentinelScenario:
    return SentinelScenario(
        name="lock_for_other_profile",
        candidate=patch_proposed(),
        plans=honest_plans(),
        tamper=_lock_for_other_profile,
        expected_verdict=ReviewVerdict.INCONCLUSIVE,
        expected_findings=(Code.IDENTITY_MISMATCH.value,),
    )


def _corrupt_artifact(context: SentinelContext) -> SentinelContext:
    diff = context.run.result.diff
    assert diff is not None
    target = context.artifacts.root / f"{diff.patch_artifact.sha256}.{PATCH_ARTIFACT_TYPE}"
    target.write_bytes(b"--- corrupted\n")
    return context


def artifact_corrupted() -> SentinelScenario:
    return SentinelScenario(
        name="artifact_corrupted",
        candidate=patch_proposed(),
        plans=honest_plans(),
        tamper=_corrupt_artifact,
        expected_verdict=ReviewVerdict.INCONCLUSIVE,
        expected_findings=(Code.EVIDENCE_INCOMPLETE.value,),
    )


def executor_unavailable() -> SentinelScenario:
    return SentinelScenario(
        name="executor_unavailable",
        candidate=patch_proposed(),
        plans=(),
        expected_verdict=ReviewVerdict.INCONCLUSIVE,
        expected_findings=(Code.EXECUTOR_UNAVAILABLE.value,),
    )


def no_test_summary() -> SentinelScenario:
    """A test run whose output carries no pytest summary is not evidence. Reproduction
    commands may be any operator check and need no summary; test runs always do."""

    profile = _profile()
    return SentinelScenario(
        name="no_test_summary",
        candidate=patch_proposed(),
        plans=[
            spec_plan(profile, CommandPurpose.REPRODUCTION, SandboxStatus.FAILED),
            spec_plan(
                profile, CommandPurpose.TARGETED_TESTS, SandboxStatus.FAILED, summary="Killed"
            ),
        ],
        expected_verdict=ReviewVerdict.INCONCLUSIVE,
        expected_findings=(Code.EVIDENCE_INCOMPLETE.value,),
    )


def sandbox_timeout() -> SentinelScenario:
    profile = _profile()
    return SentinelScenario(
        name="sandbox_timeout",
        candidate=patch_proposed(),
        plans=[
            *baseline_plans(profile),
            spec_plan(profile, CommandPurpose.TARGETED_TESTS, SandboxStatus.TIMED_OUT),
        ],
        expected_verdict=ReviewVerdict.INCONCLUSIVE,
        expected_findings=(Code.EVIDENCE_INCOMPLETE.value,),
    )


def pristine_run_mutates_tree() -> SentinelScenario:
    """The operator's own suite rewrites the pristine tree: no stable baseline exists."""

    def mutate(call: int, worktree: Path) -> None:
        if call == 2:
            (worktree / "scratch.txt").write_text("written by tests\n", encoding="utf-8")

    return SentinelScenario(
        name="pristine_run_mutates_tree",
        candidate=patch_proposed(),
        plans=honest_plans(),
        sandbox_hook=mutate,
        expected_verdict=ReviewVerdict.INCONCLUSIVE,
        expected_findings=(Code.EVIDENCE_INCOMPLETE.value,),
    )


def passing_status_with_failures() -> SentinelScenario:
    """A sandbox that reports exit 0 while its summary shows failures is not evidence."""

    return SentinelScenario(
        name="passing_status_with_failures",
        candidate=patch_proposed(),
        plans=[
            *baseline_plans(),
            *verification_plans(targeted=(SandboxStatus.SUCCEEDED, "1 failed in 0.01s")),
        ],
        expected_verdict=ReviewVerdict.INCONCLUSIVE,
        expected_findings=(Code.EVIDENCE_INCOMPLETE.value,),
    )


def default_catalog() -> list[SentinelScenario]:
    return [
        honest_fix(),
        extra_tests_added(),
        reproduction_already_passes(),
        fix_and_weaken_test(),
        test_rewritten_without_fix(),
        wrong_fix_with_rewritten_test(),
        assertion_weakened(),
        test_deleted(),
        test_renamed(),
        test_moved_outside_prefix(),
        expected_value_manipulated(),
        conftest_added(),
        pytest_config_added(),
        sitecustomize_added(),
        tests_skipped_by_code(),
        tests_not_collected(),
        verification_tree_mutated(),
        not_a_proposal(),
        source_history_rewritten(),
        lock_digest_tampered(),
        lock_entry_missing(),
        lock_names_absent_file(),
        lock_for_other_profile(),
        artifact_corrupted(),
        executor_unavailable(),
        no_test_summary(),
        sandbox_timeout(),
        pristine_run_mutates_tree(),
        passing_status_with_failures(),
    ]


def run_gate(work_root: Path, now: datetime = GATE_TIME) -> list[str]:
    """Run every scenario twice; return problems (empty means the gate passed)."""

    problems: list[str] = []
    for scenario in default_catalog():
        first = SentinelQAHarness(work_root / "first", now=now).run(scenario)
        second = SentinelQAHarness(work_root / "second", now=now).run(scenario)
        _print(first)
        problems.extend(f"{scenario.name}: {problem}" for problem in first.problems())
        if first.verdict_sha256 != second.verdict_sha256:
            problems.append(f"{scenario.name}: replay was not byte-identical")
    return problems


def _print(run: SentinelRun) -> None:
    verdict = run.verdict
    codes = ",".join(sorted(set(verdict.finding_codes))) or "-"
    print(
        f"{run.scenario.name}: candidate={run.candidate.result.outcome.value} "
        f"verdict={verdict.verdict.value} findings={codes} runs={len(verdict.runs)} "
        f"verdict_sha256={run.verdict_sha256}"
    )


def main() -> int:
    with TemporaryDirectory(prefix="sentinelqa-catalog-") as directory:
        problems = run_gate(Path(directory))
    for problem in problems:
        print(f"FAILED {problem}", file=sys.stderr)
    print("SentinelQA adversarial gate: " + ("FAILED" if problems else "passed"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
