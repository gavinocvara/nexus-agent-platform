"""PatchForgeExecutor: real PatchForge, SentinelQA, and gates over fixture repositories."""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from nexus.patchforge.e2e import FixtureRepository, materialize_fixture
from nexus.patchforge.sandbox import (
    SandboxExecution,
    SandboxExecutor,
    SandboxRequest,
    SandboxStatus,
)
from nexus.patchforge.workspace import GitRunner
from nexus.software_engineer.config import SoftwareEngineerSettings
from nexus.software_engineer.cycle import EngineeringCycle, ExecutorError
from nexus.software_engineer.executor import BRANCH_PREFIX, PatchForgeExecutor
from nexus.software_engineer.inspect import CandidateGenerator, RepositoryInspector
from nexus.software_engineer.memory import EngineerMemory, EngineerMemoryStore
from nexus.software_engineer.models import (
    CandidateEstimate,
    ChangeCategory,
    CycleDecision,
    CycleMode,
    EngineeringCandidate,
    EngineeringSignal,
    GateStatus,
    RiskLevel,
    ValidationGate,
)
from nexus.software_engineer.notify import Notifier, RecordingTransport
from nexus.software_engineer.recipes import Recipe, RecipeCommands, recipe_commands
from nexus.software_engineer.sandbox import LocalProcessSandbox

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
CYCLE = UUID(int=900)
REPOSITORY_URL = "https://example.invalid/resident/fixture"
UNFORMATTED = "def add(a,b):\n    return a+b\n"
FORMATTED = "def add(a, b):\n    return a + b\n"
UNUSED_IMPORT = "import os\n\n\ndef add(a, b):\n    return a + b\n"
TEST = "from pkg.module import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def _fixture(module: str, extra_test: str = "") -> FixtureRepository:
    return FixtureRepository(
        name="resident-fixture",
        files={
            "src/pkg/__init__.py": "",
            "src/pkg/module.py": module,
            "tests/test_module.py": TEST + extra_test,
            "pyproject.toml": "[tool.ruff]\nline-length = 88\n",
            "README.md": "# fixture\n",
        },
    )


def _commands(recipe: Recipe) -> RecipeCommands:
    return recipe_commands(recipe, typecheck=("-m", "mypy", "-p", "pkg"))


def _repository(tmp_path: Path, fixture: FixtureRepository) -> tuple[Path, GitRunner]:
    git = GitRunner(tmp_path / "git")
    repo = tmp_path / "repo"
    materialize_fixture(fixture, repo, git, NOW)
    return repo, git


def _candidate(category: ChangeCategory) -> EngineeringCandidate:
    return EngineeringCandidate(
        candidate_id=UUID(int=901),
        title="Repair source formatting drift",
        rationale="The repository's formatter check fails; its tool can repair this mechanically.",
        category=category,
        expected_paths=["src/pkg/module.py"],
        signal_ids=[UUID(int=1)],
        estimate=CandidateEstimate(value=50, urgency=60, confidence=85, cost=20),
    )


def _executor(
    tmp_path: Path, repo: Path, git: GitRunner, sandbox: SandboxExecutor | None = None
) -> PatchForgeExecutor:
    return PatchForgeExecutor(
        repo_root=repo,
        repository_url=REPOSITORY_URL,
        sandbox=sandbox or LocalProcessSandbox(allow_local_process=True, clock=lambda: NOW),
        run_root=tmp_path / "runs",
        clock=lambda: NOW,
        git=git,
        commands=_commands,
    )


def _budget() -> SoftwareEngineerSettings:
    return SoftwareEngineerSettings(_env_file=None)  # type: ignore[call-arg]


def test_formatting_recipe_produces_a_verified_branch(tmp_path: Path) -> None:
    repo, git = _repository(tmp_path, _fixture(UNFORMATTED))
    outcome = _executor(tmp_path, repo, git).execute(
        _candidate(ChangeCategory.FORMATTING), cycle_id=CYCLE, budget=_budget().budget
    )
    statuses = {item.gate: item.status for item in outcome.gates}
    assert statuses == {
        ValidationGate.RUFF_FORMAT: GateStatus.PASSED,
        ValidationGate.RUFF_LINT: GateStatus.PASSED,
        ValidationGate.MYPY: GateStatus.PASSED,
        ValidationGate.PYTEST_TARGETED: GateStatus.PASSED,
        ValidationGate.PYTEST_FULL: GateStatus.PASSED,
        ValidationGate.SENTINEL_REVIEW: GateStatus.PASSED,
    }, outcome.notes
    change = outcome.change
    assert change is not None and outcome.patch is not None
    assert change.changed_files == ["src/pkg/module.py"]
    assert change.branch == f"{BRANCH_PREFIX}/{CYCLE.hex[:12]}"
    assert change.commit_sha is not None and change.sentinel_verdict_sha256 is not None
    assert outcome.root_cause_evidence is True
    assert b"+def add(a, b):" in outcome.patch
    # The candidate branch exists in a separate clone; the operator checkout is untouched.
    branch_root = tmp_path / "runs" / str(CYCLE) / UUID(int=901).hex[:12] / "branch"
    assert (branch_root / "src" / "pkg" / "module.py").read_text() == FORMATTED
    assert (repo / "src" / "pkg" / "module.py").read_text() == UNFORMATTED
    assert git.run(["-C", str(repo), "status", "--porcelain"]).stdout == b""
    assert git.run(["-C", str(repo), "rev-parse", "HEAD"]).stdout_text().strip() == change.base_sha
    assert all(item.evidence_sha256 for item in outcome.gates)
    with pytest.raises(ExecutorError, match="not configured"):
        _executor(tmp_path / "again", repo, git).ship(change, cycle_id=CYCLE)


def test_lint_fix_recipe_removes_the_unused_import(tmp_path: Path) -> None:
    repo, git = _repository(tmp_path, _fixture(UNUSED_IMPORT))
    outcome = _executor(tmp_path, repo, git).execute(
        _candidate(ChangeCategory.DEAD_CODE_REMOVAL), cycle_id=CYCLE, budget=_budget().budget
    )
    assert outcome.change is not None and outcome.patch is not None, outcome.notes
    assert b"-import os" in outcome.patch
    assert all(item.status is GateStatus.PASSED for item in outcome.gates)


def test_a_recipe_that_breaks_a_test_is_not_proposed(tmp_path: Path) -> None:
    brittle = (
        "\n\ndef test_source_is_unformatted():\n"
        "    from pathlib import Path\n\n"
        '    assert "a+b" in Path("src/pkg/module.py").read_text()\n'
    )
    repo, git = _repository(tmp_path, _fixture(UNFORMATTED, brittle))
    outcome = _executor(tmp_path, repo, git).execute(
        _candidate(ChangeCategory.FORMATTING), cycle_id=CYCLE, budget=_budget().budget
    )
    assert outcome.change is None and outcome.patch is None
    statuses = {item.gate: item.status for item in outcome.gates}
    assert statuses[ValidationGate.PYTEST_TARGETED] is GateStatus.FAILED
    assert statuses[ValidationGate.SENTINEL_REVIEW] is GateStatus.NOT_RUN
    assert any("validation_failed" in note for note in outcome.notes)


class _SplitSandbox:
    """Passes PatchForge's runs but fails SentinelQA's verification-tree test runs."""

    def __init__(self) -> None:
        self.inner = LocalProcessSandbox(allow_local_process=True, clock=lambda: NOW)

    def execute(self, request: SandboxRequest) -> SandboxExecution:
        execution = self.inner.execute(request)
        parts = request.workspace.parts
        if "sentinelqa" in parts and "candidate" in parts and "pytest" in request.command.arguments:
            return execution.model_copy(
                update={
                    "status": SandboxStatus.FAILED,
                    "exit_code": 1,
                    "stdout": b"1 failed in 0.01s\n",
                    "error_code": "command_failed",
                }
            )
        return execution


def test_sentinelqa_disagreement_fails_the_review_gate(tmp_path: Path) -> None:
    repo, git = _repository(tmp_path, _fixture(UNFORMATTED))
    outcome = _executor(tmp_path, repo, git, sandbox=_SplitSandbox()).execute(
        _candidate(ChangeCategory.FORMATTING), cycle_id=CYCLE, budget=_budget().budget
    )
    statuses = {item.gate: item.status for item in outcome.gates}
    assert statuses[ValidationGate.PYTEST_FULL] is GateStatus.PASSED
    assert statuses[ValidationGate.SENTINEL_REVIEW] is GateStatus.FAILED
    assert any("independent_validation_disagrees" in note for note in outcome.notes)


def test_categories_without_a_recipe_return_a_plan(tmp_path: Path) -> None:
    repo, git = _repository(tmp_path, _fixture(UNFORMATTED))
    outcome = _executor(tmp_path, repo, git).execute(
        _candidate(ChangeCategory.UNKNOWN), cycle_id=CYCLE, budget=_budget().budget
    )
    assert outcome.change is None
    assert all(item.status is GateStatus.NOT_RUN for item in outcome.gates)
    assert not (tmp_path / "runs").exists()


class _OneCandidate(CandidateGenerator):
    def generate(
        self, signals: Sequence[EngineeringSignal], memories: Sequence[EngineerMemory] = ()
    ) -> list[EngineeringCandidate]:
        return [
            _candidate(ChangeCategory.FORMATTING).model_copy(
                update={"signal_ids": [signals[0].signal_id]}
            )
        ]


def test_propose_mode_cycle_turns_the_verified_branch_into_an_approval_request(
    tmp_path: Path,
) -> None:
    repo, git = _repository(tmp_path, _fixture(UNFORMATTED))
    settings = SoftwareEngineerSettings(  # type: ignore[call-arg]
        _env_file=None,
        enabled=True,
        mode=CycleMode.PROPOSE,
        state_root=tmp_path / "state",
        memory_path=tmp_path / "state" / "memory.sqlite3",
        max_runtime_seconds=3600,
    )
    transport = RecordingTransport()
    cycle = EngineeringCycle(
        settings=settings,
        inspector=RepositoryInspector(repo, git=git, clock=lambda: NOW),
        memory=EngineerMemoryStore(settings.memory_path),
        notifier=Notifier(transport, clock=lambda: NOW),
        executor=_executor(tmp_path, repo, git),
        generator=_OneCandidate(),
        clock=lambda: NOW,
        cycle_id=CYCLE,
    )
    record, report = cycle.run()
    assert record.decision is CycleDecision.REQUEST_APPROVAL, record.decision_reasons
    assert record.failure is None
    assert record.change is not None and record.change.branch is not None
    assert record.risk is not None and record.risk.level is RiskLevel.MEDIUM
    request = record.approval_request
    assert request is not None and not request.dry_run
    assert request.reference.startswith(f"branch {BRANCH_PREFIX}/")
    assert {item.gate for item in request.validation} >= {
        ValidationGate.PYTEST_FULL,
        ValidationGate.SENTINEL_REVIEW,
    }
    assert all(item.status is GateStatus.PASSED for item in request.validation)
    assert "READY FOR REVIEW\n" in transport.sent[0].body
    assert report.owner_action_required
