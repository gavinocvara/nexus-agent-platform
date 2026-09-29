"""Mechanical change recipes the resident engineer can execute without a model.

A recipe is an operator-owned ``RepositoryProfile`` plus a fixed PatchForge script. The
engine never writes files itself: the change is produced by the operator's own tool
(``ruff format`` or ``ruff check --fix``) running inside the sandbox as the profile's
formatter command, and every other command validates it. Reproduction is the matching
check command, so the defect (format drift, fixable lint) is demonstrated before the fix
and its absence after.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import HttpUrl

from nexus.patchforge.e2e import ScriptStep
from nexus.patchforge.gateway import (
    AdvancePhaseArguments,
    ListTreeArguments,
    SubmitReportArguments,
)
from nexus.patchforge.models import (
    AgentReport,
    PatchForgePhase,
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
from nexus.software_engineer.models import ChangeCategory
from nexus.software_engineer.risk import GOVERNING_PATH_PREFIXES

RECIPE_PROFILE_VERSION = 1
# The profile's image digest is a placeholder: the local process sandbox ignores it, and a
# Docker sandbox would refuse an image that does not exist locally.
_PLACEHOLDER_IMAGE = f"sha256:{'0' * 64}"


class Recipe(StrEnum):
    FORMATTING = "formatting"
    LINT_FIX = "lint_fix"
    MODEL_TYPE_FIX = "model_type_fix"
    MODEL_CODE_FIX = "model_code_fix"


RECIPE_FOR_CATEGORY: dict[ChangeCategory, Recipe] = {
    ChangeCategory.FORMATTING: Recipe.FORMATTING,
    ChangeCategory.DEAD_CODE_REMOVAL: Recipe.LINT_FIX,
}
# Recipes that need a model-backed engine. Test repair is deliberately absent: under
# SentinelQA-lite the tests are the specification and may not be changed by a candidate.
MODEL_RECIPE_FOR_CATEGORY: dict[ChangeCategory, Recipe] = {
    ChangeCategory.TYPE_ANNOTATION: Recipe.MODEL_TYPE_FIX,
    ChangeCategory.MICRO_BUG_FIX: Recipe.MODEL_CODE_FIX,
    ChangeCategory.DEFENSIVE_CHECK: Recipe.MODEL_CODE_FIX,
}
MECHANICAL_RECIPES = frozenset({Recipe.FORMATTING, Recipe.LINT_FIX})


@dataclass(frozen=True, slots=True)
class RecipeCommands:
    """Immutable argument vectors; ``python`` resolves to the runtime's interpreter."""

    formatter: tuple[str, ...]
    reproduction: tuple[str, ...]
    targeted_tests: tuple[str, ...]
    full_tests: tuple[str, ...]
    linter: tuple[str, ...]
    typecheck: tuple[str, ...]


def recipe_commands(
    recipe: Recipe,
    *,
    source_paths: Sequence[str] = ("src",),
    targeted_tests: Sequence[str] = ("tests",),
    typecheck: Sequence[str] = ("-m", "mypy"),
) -> RecipeCommands:
    sources = list(source_paths)
    targeted = ("-m", "pytest", "-q", "-p", "no:cacheprovider", *targeted_tests)
    if recipe is Recipe.FORMATTING:
        formatter = ("-m", "ruff", "format", *sources)
        reproduction = ("-m", "ruff", "format", "--check", *sources)
    elif recipe is Recipe.LINT_FIX:
        formatter = ("-m", "ruff", "check", "--fix", *sources)
        reproduction = ("-m", "ruff", "check", *sources)
    else:
        # Model recipes: the formatter only checks, so the diff is the model's alone; the
        # defect is reproduced by the check that reported it.
        formatter = ("-m", "ruff", "format", "--check", *sources)
        reproduction = tuple(typecheck) if recipe is Recipe.MODEL_TYPE_FIX else targeted
    return RecipeCommands(
        formatter=formatter,
        reproduction=reproduction,
        targeted_tests=targeted,
        full_tests=("-m", "pytest", "-q", "-p", "no:cacheprovider"),
        linter=("-m", "ruff", "check", "."),
        typecheck=tuple(typecheck),
    )


def recipe_profile(
    recipe: Recipe,
    *,
    repository_url: str,
    profile_id: str,
    commands: RecipeCommands,
    command_timeout_seconds: int = 1800,
    max_output_bytes: int = 2_000_000,
    protected_paths: Sequence[str] = GOVERNING_PATH_PREFIXES,
) -> RepositoryProfile:
    def command(arguments: tuple[str, ...]) -> SandboxCommand:
        return SandboxCommand(
            executable="python",
            arguments=list(arguments),
            timeout_seconds=command_timeout_seconds,
            max_output_bytes=max_output_bytes,
        )

    return RepositoryProfile(
        profile_id=profile_id,
        profile_version=RECIPE_PROFILE_VERSION,
        repository_url=HttpUrl(repository_url),
        sandbox=SandboxPolicy(
            image=_PLACEHOLDER_IMAGE,
            run_as_user="10001:10001",
            cpu_limit_millis=4000,
            memory_limit_mb=4096,
            pids_limit=512,
            default_timeout_seconds=command_timeout_seconds,
            max_output_bytes=max_output_bytes,
        ),
        commands={
            CommandPurpose.REPRODUCTION: command(commands.reproduction),
            CommandPurpose.TARGETED_TESTS: command(commands.targeted_tests),
            CommandPurpose.FULL_TEST_SUITE: command(commands.full_tests),
            CommandPurpose.FORMATTER: command(commands.formatter),
            CommandPurpose.LINTER: command(commands.linter),
            CommandPurpose.TYPECHECK: command(commands.typecheck),
        },
        protected_paths=sorted(set(protected_paths)),
        test_path_prefixes=["tests"],
    )


def recipe_budgets(*, phase_duration_seconds: int = 3600) -> RunBudgets:
    phase = PhaseBudget(
        max_tool_calls=12, max_duration_seconds=phase_duration_seconds, max_output_bytes=20_000_000
    )
    return RunBudgets(
        provisioning=phase,
        recon=phase,
        hypothesis=phase,
        reproduce=phase,
        implement=phase,
        targeted_validate=phase,
        full_validate=phase,
        self_review=phase,
        finalization_reserve=PhaseBudget(
            max_tool_calls=2,
            max_duration_seconds=phase_duration_seconds,
            max_output_bytes=1_000_000,
        ),
        cleanup=phase,
        max_implementation_loops=0,
        max_total_tool_calls=110,
        max_total_duration_seconds=phase_duration_seconds * 10,
    )


def recipe_steps(recipe: Recipe) -> list[ScriptStep]:
    """The fixed PatchForge script: reproduce, let the tool fix, validate twice, report."""

    if recipe not in MECHANICAL_RECIPES:
        raise ValueError("Model recipes are driven by a model-backed engine, not a script")

    def advance(target: PatchForgePhase) -> RuntimeToolAction:
        return RuntimeToolAction(
            tool_name=ToolName.ADVANCE_PHASE, arguments=AdvancePhaseArguments(target_phase=target)
        )

    def run(tool: ToolName) -> RuntimeToolAction:
        return RuntimeToolAction(tool_name=tool)

    summary = {
        Recipe.FORMATTING: "Applied the repository formatter to the source tree.",
        Recipe.LINT_FIX: "Applied the linter's safe automatic fixes to the source tree.",
    }[recipe]
    return [
        RuntimeToolAction(tool_name=ToolName.LIST_TREE, arguments=ListTreeArguments(max_depth=2)),
        advance(PatchForgePhase.HYPOTHESIS),
        advance(PatchForgePhase.REPRODUCE),
        run(ToolName.RUN_TARGETED_TESTS),
        advance(PatchForgePhase.IMPLEMENT),
        advance(PatchForgePhase.TARGETED_VALIDATE),
        run(ToolName.RUN_FORMATTER),
        run(ToolName.RUN_TARGETED_TESTS),
        advance(PatchForgePhase.FULL_VALIDATE),
        run(ToolName.RUN_FORMATTER),
        run(ToolName.RUN_LINTER),
        run(ToolName.RUN_TYPECHECK),
        run(ToolName.RUN_TEST_SUITE),
        advance(PatchForgePhase.SELF_REVIEW),
        run(ToolName.INSPECT_DIFF),
        advance(PatchForgePhase.FINALIZE),
        RuntimeToolAction(
            tool_name=ToolName.SUBMIT_REPORT,
            arguments=SubmitReportArguments(
                report=AgentReport(
                    summary=summary,
                    hypothesis=(
                        "The repository's own check reported drift that its tool can repair "
                        "mechanically."
                    ),
                    implementation=(
                        "Ran the operator profile's formatter command; no file was edited by hand."
                    ),
                    limitations=["Mechanical recipe: no semantic change was intended or reviewed."],
                )
            ),
        ),
    ]


__all__ = [
    "MECHANICAL_RECIPES",
    "MODEL_RECIPE_FOR_CATEGORY",
    "RECIPE_FOR_CATEGORY",
    "RECIPE_PROFILE_VERSION",
    "Recipe",
    "RecipeCommands",
    "recipe_budgets",
    "recipe_commands",
    "recipe_profile",
    "recipe_steps",
]
