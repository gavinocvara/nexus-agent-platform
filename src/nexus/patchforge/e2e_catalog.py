"""Deterministic PatchForge end-to-end scenarios over a synthetic calculator fixture."""

from __future__ import annotations

from hashlib import sha256

from pydantic import HttpUrl

from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.e2e import E2EScenario, FixtureRepository, ScriptStep
from nexus.patchforge.gateway import (
    AdvancePhaseArguments,
    ListTreeArguments,
    ReadFileRangeArguments,
    SubmitReportArguments,
    WritePatchArguments,
)
from nexus.patchforge.models import (
    AgentReport,
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
from nexus.patchforge.runtime import RuntimeToolAction
from nexus.patchforge.sandbox import FakeSandboxPlan, SandboxStatus

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


def default_catalog() -> list[E2EScenario]:
    """Every scenario the deterministic E2E gate must reproduce."""

    return [patch_proposed()]
