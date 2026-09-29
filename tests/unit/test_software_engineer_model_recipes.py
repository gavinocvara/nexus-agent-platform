"""Model-backed recipes: the same governance as mechanical ones, with zero live calls."""

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from nexus.patchforge.e2e import FixtureRepository, materialize_fixture
from nexus.patchforge.engine import EngineBudget, ModelBackedEngine, ModelRequest, ModelResponse
from nexus.patchforge.gateway import (
    AdvancePhaseArguments,
    ListTreeArguments,
    ReadFileRangeArguments,
    SubmitReportArguments,
    WritePatchArguments,
)
from nexus.patchforge.models import AgentReport, PatchForgePhase, ToolName
from nexus.patchforge.runtime import RuntimeToolAction
from nexus.patchforge.workspace import GitRunner
from nexus.software_engineer import __main__ as cli
from nexus.software_engineer.config import SoftwareEngineerSettings
from nexus.software_engineer.executor import PatchForgeExecutor
from nexus.software_engineer.models import (
    CandidateEstimate,
    ChangeCategory,
    EngineeringCandidate,
    GateStatus,
    ValidationGate,
)
from nexus.software_engineer.recipes import (
    MODEL_RECIPE_FOR_CATEGORY,
    Recipe,
    RecipeCommands,
    recipe_commands,
    recipe_steps,
)
from nexus.software_engineer.sandbox import LocalProcessSandbox

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
CYCLE = UUID(int=950)
REPOSITORY_URL = "https://example.invalid/resident/model-fixture"
BROKEN = "def add(a: int, b: int) -> int:\n    return str(a + b)\n"
FIXED = "def add(a: int, b: int) -> int:\n    return a + b\n"
TEST = "from pkg.module import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
FIXTURE = FixtureRepository(
    name="model-fixture",
    files={
        "src/pkg/__init__.py": "",
        "src/pkg/module.py": BROKEN,
        "tests/test_module.py": TEST,
        "pyproject.toml": "[tool.ruff]\nline-length = 88\n",
    },
)


class ScriptedModel:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(text=self.replies.pop(0), input_tokens=11, output_tokens=7)


def _reply(action: RuntimeToolAction) -> str:
    return json.dumps(
        {"tool": action.tool_name.value, "arguments_json": action.arguments.model_dump_json()}
    )


def _advance(target: PatchForgePhase) -> RuntimeToolAction:
    return RuntimeToolAction(
        tool_name=ToolName.ADVANCE_PHASE, arguments=AdvancePhaseArguments(target_phase=target)
    )


def _type_fix_script() -> list[RuntimeToolAction]:
    return [
        RuntimeToolAction(tool_name=ToolName.LIST_TREE, arguments=ListTreeArguments(max_depth=2)),
        _advance(PatchForgePhase.HYPOTHESIS),
        RuntimeToolAction(
            tool_name=ToolName.READ_FILE_RANGE,
            arguments=ReadFileRangeArguments(path="src/pkg/module.py", start_line=1, end_line=5),
        ),
        _advance(PatchForgePhase.REPRODUCE),
        RuntimeToolAction(tool_name=ToolName.RUN_TARGETED_TESTS),
        _advance(PatchForgePhase.IMPLEMENT),
        RuntimeToolAction(
            tool_name=ToolName.WRITE_PATCH,
            arguments=WritePatchArguments(
                path="src/pkg/module.py",
                expected_sha256=sha256(BROKEN.encode()).hexdigest(),
                content=FIXED,
            ),
        ),
        _advance(PatchForgePhase.TARGETED_VALIDATE),
        RuntimeToolAction(tool_name=ToolName.RUN_TARGETED_TESTS),
        _advance(PatchForgePhase.FULL_VALIDATE),
        RuntimeToolAction(tool_name=ToolName.RUN_FORMATTER),
        RuntimeToolAction(tool_name=ToolName.RUN_LINTER),
        RuntimeToolAction(tool_name=ToolName.RUN_TYPECHECK),
        RuntimeToolAction(tool_name=ToolName.RUN_TEST_SUITE),
        _advance(PatchForgePhase.SELF_REVIEW),
        RuntimeToolAction(tool_name=ToolName.INSPECT_DIFF),
        _advance(PatchForgePhase.FINALIZE),
        RuntimeToolAction(
            tool_name=ToolName.SUBMIT_REPORT,
            arguments=SubmitReportArguments(
                report=AgentReport(
                    summary="Return the integer sum instead of its string form.",
                    hypothesis="The function converted the sum to str, violating its annotation.",
                    implementation="Removed the str() call.",
                )
            ),
        ),
    ]


def _commands(recipe: Recipe) -> RecipeCommands:
    return recipe_commands(recipe, typecheck=("-m", "mypy", "-p", "pkg"))


def _candidate(category: ChangeCategory = ChangeCategory.TYPE_ANNOTATION) -> EngineeringCandidate:
    return EngineeringCandidate(
        candidate_id=UUID(int=951),
        title="Fix the mypy error in pkg.module",
        rationale="mypy reports an incompatible return type in src/pkg/module.py.",
        category=category,
        expected_paths=["src/pkg/module.py"],
        signal_ids=[UUID(int=1)],
        estimate=CandidateEstimate(value=60, urgency=70, confidence=70, cost=30),
    )


def _executor(
    tmp_path: Path, replies: list[str] | None
) -> tuple[PatchForgeExecutor, Path, GitRunner]:
    git = GitRunner(tmp_path / "git")
    repo = tmp_path / "repo"
    materialize_fixture(FIXTURE, repo, git, NOW)
    budget = EngineBudget(
        max_model_calls=40,
        max_output_tokens=512,
        max_rendered_result_chars=4000,
        max_history_turns=6,
    )
    factory = (
        None
        if replies is None
        else (
            lambda: ModelBackedEngine(client=ScriptedModel(replies), model="fixture", budget=budget)
        )
    )
    executor = PatchForgeExecutor(
        repo_root=repo,
        repository_url=REPOSITORY_URL,
        sandbox=LocalProcessSandbox(allow_local_process=True, clock=lambda: NOW),
        run_root=tmp_path / "runs",
        clock=lambda: NOW,
        git=git,
        commands=_commands,
        model_engine_factory=factory,
    )
    return executor, repo, git


def test_model_recipe_fixes_a_type_error_under_full_governance(tmp_path: Path) -> None:
    replies = [_reply(action) for action in _type_fix_script()]
    executor, repo, git = _executor(tmp_path, replies)
    budget = SoftwareEngineerSettings.model_validate({}).budget
    outcome = executor.execute(_candidate(), cycle_id=CYCLE, budget=budget)
    statuses = {item.gate: item.status for item in outcome.gates}
    assert all(status is GateStatus.PASSED for status in statuses.values()), outcome.notes
    assert statuses[ValidationGate.SENTINEL_REVIEW] is GateStatus.PASSED
    assert outcome.change is not None and outcome.patch is not None
    assert b"-    return str(a + b)" in outcome.patch and b"+    return a + b" in outcome.patch
    assert outcome.model_calls == len(replies)
    assert outcome.input_tokens == 11 * len(replies) and outcome.output_tokens == 7 * len(replies)
    assert outcome.root_cause_evidence is True
    run_root = tmp_path / "runs" / str(CYCLE) / UUID(int=951).hex[:12]
    calls = json.loads((run_root / "engine_calls.json").read_text())["calls"]
    assert len(calls) == len(replies) and all("request_sha256" in item for item in calls)
    assert not any("prompt" in item or "text" in item for item in calls)
    assert (repo / "src" / "pkg" / "module.py").read_text() == BROKEN
    assert git.run(["-C", str(repo), "status", "--porcelain"]).stdout == b""


def test_model_recipe_without_a_configured_model_is_a_plan(tmp_path: Path) -> None:
    executor, _, _ = _executor(tmp_path, None)
    outcome = executor.execute(
        _candidate(), cycle_id=CYCLE, budget=SoftwareEngineerSettings.model_validate({}).budget
    )
    assert outcome.change is None and outcome.model_calls == 0
    assert all(item.status is GateStatus.NOT_RUN for item in outcome.gates)
    assert any("no model is configured" in note for note in outcome.notes)
    assert not (tmp_path / "runs").exists()


def test_model_garbage_is_a_typed_engine_failure_not_a_change(tmp_path: Path) -> None:
    executor, _, _ = _executor(tmp_path, ["garbage"] * 3)
    outcome = executor.execute(
        _candidate(ChangeCategory.MICRO_BUG_FIX),
        cycle_id=CYCLE,
        budget=SoftwareEngineerSettings.model_validate({}).budget,
    )
    assert outcome.change is None
    # One rejected action, then the runtime's finalization turn: bounded, no retries.
    assert 1 <= outcome.model_calls <= 2
    assert any("engine_error" in note for note in outcome.notes)
    assert {item.status for item in outcome.gates} == {GateStatus.NOT_RUN}


def test_model_recipes_exclude_test_repair_and_need_a_model_engine() -> None:
    assert ChangeCategory.TEST_REPAIR not in MODEL_RECIPE_FOR_CATEGORY
    assert set(MODEL_RECIPE_FOR_CATEGORY) == {
        ChangeCategory.TYPE_ANNOTATION,
        ChangeCategory.MICRO_BUG_FIX,
        ChangeCategory.DEFENSIVE_CHECK,
    }
    with pytest.raises(ValueError, match="model-backed engine"):
        recipe_steps(Recipe.MODEL_TYPE_FIX)
    commands = recipe_commands(Recipe.MODEL_TYPE_FIX, typecheck=("-m", "mypy"))
    assert commands.reproduction == ("-m", "mypy")
    assert commands.formatter == ("-m", "ruff", "format", "--check", "src")


def test_cli_never_builds_a_model_engine_without_every_explicit_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in list(os.environ):
        if key.startswith("NEXUS_SOFTWARE_ENGINEER_") or key == "OPENAI_API_KEY":
            monkeypatch.delenv(key)
    base = {"enabled": True, "mode": "propose", "sandbox": "local_process"}
    assert cli._model_engine_factory(SoftwareEngineerSettings.model_validate(base)) is None
    with_model = {**base, "model": "gpt-test", "max_model_calls": 5, "max_output_tokens": 500}
    assert cli._model_engine_factory(SoftwareEngineerSettings.model_validate(with_model)) is None
    confirmed = {**with_model, "confirm_model_spend": True}
    assert cli._model_engine_factory(SoftwareEngineerSettings.model_validate(confirmed)) is None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-only-never-used")
    factory = cli._model_engine_factory(SoftwareEngineerSettings.model_validate(confirmed))
    assert factory is not None
    engine = factory()
    assert isinstance(engine, ModelBackedEngine) and engine.budget.max_model_calls == 5
    assert engine.records == []
    zero_budget = {**confirmed, "max_model_calls": 0}
    assert cli._model_engine_factory(SoftwareEngineerSettings.model_validate(zero_budget)) is None
    # A workflow passes unset variables as empty strings: that is "no model", not an error.
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_MODEL", "   ")
    blank = SoftwareEngineerSettings(_env_file=None)  # type: ignore[call-arg]
    assert blank.model is None and blank.model_recipes_allowed is False
