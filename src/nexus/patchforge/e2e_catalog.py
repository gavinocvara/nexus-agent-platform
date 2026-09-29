"""Deterministic PatchForge end-to-end scenarios over a synthetic calculator fixture."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from pydantic import HttpUrl

from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.e2e import (
    E2EScenario,
    FixtureRepository,
    PatchForgeE2EHarness,
    ScriptStep,
)
from nexus.patchforge.gateway import (
    AdvancePhaseArguments,
    ListTreeArguments,
    ReadFileRangeArguments,
    SubmitReportArguments,
    WritePatchArguments,
)
from nexus.patchforge.models import (
    AgentReport,
    PatchForgeFailure,
    PatchForgePhase,
    PatchOutcome,
    PhaseBudget,
    RunBudgets,
    ToolName,
)
from nexus.patchforge.policy import (
    CommandPurpose,
    RepositoryProfile,
    SandboxCommand,
    SandboxPolicy,
)
from nexus.patchforge.runtime import RuntimeCancelledError, RuntimeToolAction
from nexus.patchforge.sandbox import FakeSandboxPlan, SandboxStatus

GATE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
BROKEN = "def subtract(a, b):\n    return a + b\n"
FIXED = "def subtract(a, b):\n    return a - b\n"
CALCULATOR = FixtureRepository(
    name="calculator",
    files={
        "calculator.py": BROKEN,
        "tests/test_calculator.py": (
            "from calculator import subtract\n\n\n"
            "def test_subtract():\n    assert subtract(5, 2) == 3\n"
        ),
        "protected.txt": "operator owned\n",
    },
    commit_message="fixture: subtraction adds its operands",
)


def calculator_profile() -> RepositoryProfile:
    return RepositoryProfile(
        profile_id="e2e.calculator",
        profile_version=1,
        repository_url=HttpUrl("https://example.invalid/patchforge/calculator"),
        sandbox=SandboxPolicy(
            image=f"sha256:{'e' * 64}",
            run_as_user="10001:10001",
            cpu_limit_millis=1000,
            memory_limit_mb=256,
            pids_limit=64,
            default_timeout_seconds=60,
            max_output_bytes=100_000,
        ),
        commands={
            CommandPurpose.REPRODUCTION: SandboxCommand(
                executable="python",
                arguments=["-m", "pytest", "-q", "tests/test_calculator.py"],
                timeout_seconds=30,
                max_output_bytes=100_000,
            ),
            CommandPurpose.TARGETED_TESTS: SandboxCommand(
                executable="python",
                arguments=["-m", "pytest", "-q", "tests/test_calculator.py", "-x"],
                timeout_seconds=30,
                max_output_bytes=100_000,
            ),
            CommandPurpose.FULL_TEST_SUITE: SandboxCommand(
                executable="python",
                arguments=["-m", "pytest", "-q"],
                timeout_seconds=30,
                max_output_bytes=100_000,
            ),
        },
        protected_paths=["protected.txt"],
        test_path_prefixes=["tests"],
    )


def calculator_budgets(*, loops: int = 1) -> RunBudgets:
    budget = PhaseBudget(max_tool_calls=10, max_duration_seconds=60, max_output_bytes=100_000)
    return RunBudgets(
        provisioning=budget,
        recon=budget,
        hypothesis=budget,
        reproduce=budget,
        implement=budget,
        targeted_validate=budget,
        full_validate=budget,
        self_review=budget,
        finalization_reserve=PhaseBudget(
            max_tool_calls=2, max_duration_seconds=60, max_output_bytes=100_000
        ),
        cleanup=budget,
        max_implementation_loops=loops,
        max_total_tool_calls=92,
        max_total_duration_seconds=600,
    )


def plan(
    purpose: CommandPurpose, status: SandboxStatus, profile: RepositoryProfile | None = None
) -> FakeSandboxPlan:
    """A scripted sandbox result for one operator-profile command."""

    selected = profile or calculator_profile()
    command_sha = canonical_sha256(selected.commands[purpose])
    if status is SandboxStatus.SUCCEEDED:
        return FakeSandboxPlan(
            expected_command_sha256=command_sha,
            status=status,
            exit_code=0,
            stdout=b"1 passed\n",
        )
    if status is SandboxStatus.FAILED:
        return FakeSandboxPlan(
            expected_command_sha256=command_sha,
            status=status,
            exit_code=1,
            stdout=b"1 failed\n",
            stderr=b"assert 7 == 3\n",
            error_code="command_failed",
        )
    return FakeSandboxPlan(
        expected_command_sha256=command_sha,
        status=status,
        error_code="sandbox_error",
    )


def advance(target: PatchForgePhase) -> RuntimeToolAction:
    return RuntimeToolAction(
        tool_name=ToolName.ADVANCE_PHASE,
        arguments=AdvancePhaseArguments(target_phase=target),
    )


def run(tool_name: ToolName) -> RuntimeToolAction:
    return RuntimeToolAction(tool_name=tool_name)


def write(content: str, previous: str, path: str = "calculator.py") -> RuntimeToolAction:
    return RuntimeToolAction(
        tool_name=ToolName.WRITE_PATCH,
        arguments=WritePatchArguments(
            path=path,
            expected_sha256=sha256(previous.encode("utf-8")).hexdigest(),
            content=content,
        ),
    )


def report() -> RuntimeToolAction:
    return RuntimeToolAction(
        tool_name=ToolName.SUBMIT_REPORT,
        arguments=SubmitReportArguments(
            report=AgentReport(
                summary="Subtraction now subtracts.",
                hypothesis="subtract() added its operands instead of subtracting them.",
                implementation="Replaced the addition with a subtraction.",
            )
        ),
    )


def happy_steps() -> list[ScriptStep]:
    """The complete scripted path from reconnaissance through the report."""

    return [
        RuntimeToolAction(tool_name=ToolName.LIST_TREE, arguments=ListTreeArguments()),
        advance(PatchForgePhase.HYPOTHESIS),
        RuntimeToolAction(
            tool_name=ToolName.READ_FILE_RANGE,
            arguments=ReadFileRangeArguments(path="calculator.py", start_line=1, end_line=2),
        ),
        advance(PatchForgePhase.REPRODUCE),
        run(ToolName.RUN_TARGETED_TESTS),
        advance(PatchForgePhase.IMPLEMENT),
        write(FIXED, BROKEN),
        advance(PatchForgePhase.TARGETED_VALIDATE),
        run(ToolName.RUN_TARGETED_TESTS),
        advance(PatchForgePhase.FULL_VALIDATE),
        run(ToolName.RUN_TEST_SUITE),
        advance(PatchForgePhase.SELF_REVIEW),
        run(ToolName.INSPECT_DIFF),
        advance(PatchForgePhase.FINALIZE),
        report(),
    ]


def happy_plans() -> list[FakeSandboxPlan]:
    return [
        plan(CommandPurpose.REPRODUCTION, SandboxStatus.FAILED),
        plan(CommandPurpose.TARGETED_TESTS, SandboxStatus.SUCCEEDED),
        plan(CommandPurpose.FULL_TEST_SUITE, SandboxStatus.SUCCEEDED),
    ]


def patch_proposed() -> E2EScenario:
    return E2EScenario(
        name="patch_proposed",
        fixture=CALCULATOR,
        profile=calculator_profile(),
        budgets=calculator_budgets(),
        steps=happy_steps(),
        sandbox_plans=happy_plans(),
        expected_outcome=PatchOutcome.PATCH_PROPOSED,
        expected_failure=None,
    )


def _scenario(
    name: str,
    steps: list[ScriptStep],
    plans: list[FakeSandboxPlan],
    outcome: PatchOutcome,
    failure: PatchForgeFailure,
    **options: Any,
) -> E2EScenario:
    return E2EScenario(
        name=name,
        fixture=CALCULATOR,
        profile=calculator_profile(),
        budgets=calculator_budgets(),
        steps=steps,
        sandbox_plans=plans,
        expected_outcome=outcome,
        expected_failure=failure,
        **options,
    )


def validation_failed() -> E2EScenario:
    """Targeted tests still fail on the final tree; the report is attested as partial."""

    plans = happy_plans()
    plans[1] = plan(CommandPurpose.TARGETED_TESTS, SandboxStatus.FAILED)
    return _scenario(
        "validation_failed",
        happy_steps(),
        plans,
        PatchOutcome.PARTIAL,
        PatchForgeFailure.VALIDATION_FAILED,
    )


def budget_exhausted() -> E2EScenario:
    """A second implementation retry exceeds the one-loop budget."""

    return _scenario(
        "budget_exhausted",
        [
            *happy_steps()[:9],
            advance(PatchForgePhase.IMPLEMENT),
            advance(PatchForgePhase.TARGETED_VALIDATE),
            advance(PatchForgePhase.IMPLEMENT),
            report(),
        ],
        [
            plan(CommandPurpose.REPRODUCTION, SandboxStatus.FAILED),
            plan(CommandPurpose.TARGETED_TESTS, SandboxStatus.FAILED),
        ],
        PatchOutcome.PARTIAL,
        PatchForgeFailure.BUDGET_EXHAUSTED,
        expected_phase_reached=PatchForgePhase.TARGETED_VALIDATE,
        expected_implementation_loops=1,
    )


def policy_denied() -> E2EScenario:
    """The engine tries to edit an operator-protected file; the gateway denies it."""

    return _scenario(
        "policy_denied",
        [
            *happy_steps()[:6],
            write("changed\n", CALCULATOR.files["protected.txt"], path="protected.txt"),
            report(),
        ],
        happy_plans()[:1],
        PatchOutcome.POLICY_VIOLATION,
        PatchForgeFailure.POLICY_DENIED,
        expected_phase_reached=PatchForgePhase.IMPLEMENT,
    )


def cancelled() -> E2EScenario:
    return _scenario(
        "cancelled",
        [*happy_steps()[:3], RuntimeCancelledError("operator cancelled the run"), report()],
        [],
        PatchOutcome.CANCELLED,
        PatchForgeFailure.CANCELLED,
        expected_phase_reached=PatchForgePhase.HYPOTHESIS,
    )


def sandbox_error() -> E2EScenario:
    return _scenario(
        "sandbox_error",
        [*happy_steps()[:5], report()],
        [plan(CommandPurpose.REPRODUCTION, SandboxStatus.SANDBOX_ERROR)],
        PatchOutcome.SANDBOX_FAILED,
        PatchForgeFailure.SANDBOX_ERROR,
        expected_phase_reached=PatchForgePhase.REPRODUCE,
    )


def workspace_error() -> E2EScenario:
    """Code in the sandbox rewrites ignore rules; the runtime fingerprint fails closed."""

    def write_ignore_rules(call: int, worktree: Path) -> None:
        if call == 1:
            (worktree / ".gitignore").write_text("*.py\n", encoding="utf-8")

    return _scenario(
        "workspace_error",
        [*happy_steps()[:5], report()],
        happy_plans()[:1],
        PatchOutcome.ABORTED,
        PatchForgeFailure.WORKSPACE_ERROR,
        sandbox_hook=write_ignore_rules,
        expected_phase_reached=PatchForgePhase.REPRODUCE,
    )


def engine_error() -> E2EScenario:
    return _scenario(
        "engine_error",
        [*happy_steps()[:3], RuntimeError("engine adapter crashed"), report()],
        [],
        PatchOutcome.ABORTED,
        PatchForgeFailure.ENGINE_ERROR,
        expected_phase_reached=PatchForgePhase.HYPOTHESIS,
    )


def attestation_failed() -> E2EScenario:
    """The tree changes between recorded calls; the run completes but cannot be attested."""

    def tamper(worktree: Path) -> None:
        (worktree / "calculator.py").write_text(FIXED + "# out of band\n", encoding="utf-8")

    steps = happy_steps()
    steps.insert(10, tamper)
    return _scenario(
        "attestation_failed",
        steps,
        happy_plans(),
        PatchOutcome.ABORTED,
        PatchForgeFailure.ATTESTATION_FAILED,
        expected_findings=("workspace_changed_out_of_band",),
    )


def cleanup_failed() -> E2EScenario:
    return _scenario(
        "cleanup_failed",
        happy_steps(),
        happy_plans(),
        PatchOutcome.ABORTED,
        PatchForgeFailure.CLEANUP_FAILED,
        fail_cleanup=True,
        expected_phase_reached=PatchForgePhase.CLEANUP,
    )


WRONG = "def subtract(a, b):\n    return b - a\n"


def reproduction_not_demonstrated() -> E2EScenario:
    """The defect cannot be reproduced: the reproduction already passes. A warning only."""

    plans = happy_plans()
    plans[0] = plan(CommandPurpose.REPRODUCTION, SandboxStatus.SUCCEEDED)
    return E2EScenario(
        name="reproduction_not_demonstrated",
        fixture=CALCULATOR,
        profile=calculator_profile(),
        budgets=calculator_budgets(),
        steps=happy_steps(),
        sandbox_plans=plans,
        expected_outcome=PatchOutcome.PATCH_PROPOSED,
        expected_failure=None,
        expected_findings=("reproduction_not_demonstrated",),
    )


def reproduction_missing() -> E2EScenario:
    """The engine skips reproduction although the profile makes it practical."""

    steps = happy_steps()
    del steps[4]
    return _scenario(
        "reproduction_missing",
        steps,
        happy_plans()[1:],
        PatchOutcome.PARTIAL,
        PatchForgeFailure.VALIDATION_FAILED,
        expected_findings=("reproduction_missing",),
    )


def targeted_retry_succeeds() -> E2EScenario:
    """A wrong first fix fails targeted tests; one bounded retry fixes it."""

    return E2EScenario(
        name="targeted_retry_succeeds",
        fixture=CALCULATOR,
        profile=calculator_profile(),
        budgets=calculator_budgets(),
        steps=[
            *happy_steps()[:6],
            write(WRONG, BROKEN),
            advance(PatchForgePhase.TARGETED_VALIDATE),
            run(ToolName.RUN_TARGETED_TESTS),
            advance(PatchForgePhase.IMPLEMENT),
            write(FIXED, WRONG),
            *happy_steps()[7:],
        ],
        sandbox_plans=[
            plan(CommandPurpose.REPRODUCTION, SandboxStatus.FAILED),
            plan(CommandPurpose.TARGETED_TESTS, SandboxStatus.FAILED),
            plan(CommandPurpose.TARGETED_TESTS, SandboxStatus.SUCCEEDED),
            plan(CommandPurpose.FULL_TEST_SUITE, SandboxStatus.SUCCEEDED),
        ],
        expected_outcome=PatchOutcome.PATCH_PROPOSED,
        expected_failure=None,
        expected_implementation_loops=1,
    )


def full_validation_failed() -> E2EScenario:
    plans = happy_plans()
    plans[2] = plan(CommandPurpose.FULL_TEST_SUITE, SandboxStatus.FAILED)
    return _scenario(
        "full_validation_failed",
        happy_steps(),
        plans,
        PatchOutcome.PARTIAL,
        PatchForgeFailure.VALIDATION_FAILED,
    )


def stale_validation() -> E2EScenario:
    """Targeted tests pass, then the code changes again without re-running them."""

    revised = FIXED.replace("a - b", "a - b  # revised")
    return _scenario(
        "stale_validation",
        [
            *happy_steps()[:9],
            advance(PatchForgePhase.IMPLEMENT),
            write(revised, FIXED),
            advance(PatchForgePhase.TARGETED_VALIDATE),
            *happy_steps()[9:],
        ],
        happy_plans(),
        PatchOutcome.PARTIAL,
        PatchForgeFailure.VALIDATION_FAILED,
        expected_implementation_loops=1,
        expected_findings=("stale_validation",),
    )


def lease_renewal_failed() -> E2EScenario:
    """The workspace lease cannot be renewed before the reproduction run."""

    return _scenario(
        "lease_renewal_failed",
        [*happy_steps()[:5], report()],
        [],
        PatchOutcome.ABORTED,
        PatchForgeFailure.WORKSPACE_ERROR,
        fail_lease_renewal_at=3,
        expected_phase_reached=PatchForgePhase.REPRODUCE,
    )


def finalization_failed() -> E2EScenario:
    """The engine fails inside finalization; cleanup still runs and closes the run."""

    return _scenario(
        "finalization_failed",
        [*happy_steps()[:14], RuntimeError("engine failed while finalizing")],
        happy_plans(),
        PatchOutcome.ABORTED,
        PatchForgeFailure.ENGINE_ERROR,
        expected_phase_reached=PatchForgePhase.FINALIZE,
    )


def unknown_report_evidence() -> E2EScenario:
    """The report cites evidence that does not exist; the gateway refuses it."""

    invalid = RuntimeToolAction(
        tool_name=ToolName.SUBMIT_REPORT,
        arguments=SubmitReportArguments(
            report=AgentReport(
                summary="Claims evidence that was never recorded.",
                hypothesis="Not applicable.",
                implementation="Not applicable.",
                evidence_references=[UUID(int=999)],
            )
        ),
    )
    return _scenario(
        "unknown_report_evidence",
        [*happy_steps()[:14], invalid],
        happy_plans(),
        PatchOutcome.POLICY_VIOLATION,
        PatchForgeFailure.POLICY_DENIED,
        expected_phase_reached=PatchForgePhase.FINALIZE,
    )


def tamper_after_last_call() -> E2EScenario:
    """The tree changes after the last recorded call; the final capture cannot be attested."""

    def tamper(worktree: Path) -> None:
        (worktree / "calculator.py").write_text(FIXED + "# late edit\n", encoding="utf-8")

    steps = happy_steps()
    steps.insert(13, tamper)
    return _scenario(
        "tamper_after_last_call",
        steps,
        happy_plans(),
        PatchOutcome.ABORTED,
        PatchForgeFailure.ATTESTATION_FAILED,
        expected_findings=("final_state_unobserved",),
    )


def finalization_reserve_used() -> E2EScenario:
    """A finalize-phase read may use the reserve, but never the report's last call."""

    return E2EScenario(
        name="finalization_reserve_used",
        fixture=CALCULATOR,
        profile=calculator_profile(),
        budgets=calculator_budgets(),
        steps=[*happy_steps()[:14], run(ToolName.GIT_STATUS), report()],
        sandbox_plans=happy_plans(),
        expected_outcome=PatchOutcome.PATCH_PROPOSED,
        expected_failure=None,
    )


def finalization_reserve_protected() -> E2EScenario:
    """A second finalize-phase read would take the report's reserved call. It is refused,
    classified as budget exhaustion, and the reserved report is still accepted."""

    return _scenario(
        "finalization_reserve_protected",
        [
            *happy_steps()[:14],
            run(ToolName.GIT_STATUS),
            run(ToolName.GIT_STATUS),
            report(),
        ],
        happy_plans(),
        PatchOutcome.PARTIAL,
        PatchForgeFailure.BUDGET_EXHAUSTED,
        expected_phase_reached=PatchForgePhase.FINALIZE,
    )


def reserve_refusal_then_other_action() -> E2EScenario:
    """After a reserve refusal, a further non-report action ends finalization."""

    return _scenario(
        "reserve_refusal_then_other_action",
        [
            *happy_steps()[:14],
            run(ToolName.GIT_STATUS),
            run(ToolName.GIT_STATUS),
            run(ToolName.INSPECT_DIFF),
            report(),
        ],
        happy_plans(),
        PatchOutcome.PARTIAL,
        PatchForgeFailure.BUDGET_EXHAUSTED,
        expected_phase_reached=PatchForgePhase.FINALIZE,
    )


def second_report_attempt() -> E2EScenario:
    """The run ends at the first accepted report; a scripted second report is never used."""

    return E2EScenario(
        name="second_report_attempt",
        fixture=CALCULATOR,
        profile=calculator_profile(),
        budgets=calculator_budgets(),
        steps=[*happy_steps(), report()],
        sandbox_plans=happy_plans(),
        expected_outcome=PatchOutcome.PATCH_PROPOSED,
        expected_failure=None,
    )


def default_catalog() -> list[E2EScenario]:
    """Every scenario the deterministic E2E gate must reproduce: success plus one
    scenario per failure classification."""

    return [
        patch_proposed(),
        validation_failed(),
        budget_exhausted(),
        policy_denied(),
        cancelled(),
        sandbox_error(),
        workspace_error(),
        engine_error(),
        attestation_failed(),
        cleanup_failed(),
        reproduction_not_demonstrated(),
        reproduction_missing(),
        targeted_retry_succeeds(),
        full_validation_failed(),
        stale_validation(),
        lease_renewal_failed(),
        finalization_failed(),
        unknown_report_evidence(),
        tamper_after_last_call(),
        finalization_reserve_used(),
        finalization_reserve_protected(),
        reserve_refusal_then_other_action(),
        second_report_attempt(),
    ]


def run_gate(work_root: Path, now: datetime = GATE_TIME) -> list[str]:
    """Run every scenario twice; return mismatch descriptions (empty means the gate passed)."""

    problems: list[str] = []
    for scenario in default_catalog():
        first = PatchForgeE2EHarness(work_root / "first", now=now).run(scenario)
        second = PatchForgeE2EHarness(work_root / "second", now=now).run(scenario)
        failure = first.result.failure.value if first.result.failure else "none"
        print(
            f"{scenario.name}: outcome={first.result.outcome.value} failure={failure} "
            f"phase={first.result.phase_reached.value} "
            f"loops={first.completion.snapshot.implementation_loops} "
            f"result_sha256={first.result_sha256}"
        )
        problems.extend(f"{scenario.name}: {problem}" for problem in first.problems())
        if first.result_sha256 != second.result_sha256:
            problems.append(f"{scenario.name}: replay was not byte-identical")
    return problems


def main() -> int:
    with TemporaryDirectory(prefix="patchforge-e2e-") as directory:
        problems = run_gate(Path(directory))
    for problem in problems:
        print(f"FAILED {problem}", file=sys.stderr)
    print("PatchForge deterministic E2E gate: " + ("FAILED" if problems else "passed"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
