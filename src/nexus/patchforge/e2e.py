"""Deterministic end-to-end PatchForge harness over scripted engines and fixture repositories.

The harness composes the production path (WorkspaceManager, ToolGateway, a sandbox
executor, PatchForgeRuntime, and PatchForgeAttestor) around a scripted engine and a
``FakeSandbox``. It makes no model calls and needs no network, Docker, or secrets. Fixed
clocks, content-derived identifiers, and fixed Git identities make a scenario replay to a
byte-identical ``PatchResult``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

from nexus.atlas.models import SourceRevision
from nexus.patchforge.attestor import LocalArtifactStore, PatchForgeAttestor
from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.gateway import ToolGateway
from nexus.patchforge.models import (
    EngineeringTask,
    PatchForgeFailure,
    PatchOutcome,
    PatchResult,
    RunBudgets,
    RunIdentity,
    ToolName,
)
from nexus.patchforge.policy import PatchForgePolicy, RepositoryProfile
from nexus.patchforge.runtime import (
    PatchForgeRuntime,
    RuntimeCompletion,
    RuntimeToolAction,
    RuntimeTurn,
)
from nexus.patchforge.sandbox import (
    FakeSandbox,
    FakeSandboxPlan,
    SandboxExecution,
    SandboxRequest,
)
from nexus.patchforge.workspace import GitRunner, WorkspaceError, WorkspaceManager

_E2E_NAMESPACE = UUID("8d3c2f64-0b1e-4c55-9a27-2f6e1d0c9b31")
FIXTURE_AUTHOR_NAME = "PatchForge Fixture"
FIXTURE_AUTHOR_EMAIL = "patchforge-fixture@nexus.invalid"

WorkspaceHook = Callable[[Path], None]
"""Fault injection that changes the worktree outside any tool call."""

SandboxHook = Callable[[int, Path], None]
"""Fault injection run inside the Nth sandbox execution, before its planned result."""

ScriptStep = RuntimeToolAction | Exception | WorkspaceHook


@dataclass(frozen=True, slots=True)
class FixtureRepository:
    """A synthetic source repository described as data."""

    name: str
    files: Mapping[str, str]
    commit_message: str = "fixture: initial state"


@dataclass(frozen=True, slots=True)
class E2EScenario:
    """One scripted run and the outcome its runtime-owned evidence must produce."""

    name: str
    fixture: FixtureRepository
    profile: RepositoryProfile
    budgets: RunBudgets
    steps: Sequence[ScriptStep]
    sandbox_plans: Sequence[FakeSandboxPlan]
    expected_outcome: PatchOutcome
    expected_failure: PatchForgeFailure | None
    title: str = "Repair the fixture defect"
    instructions: str = "Make the fixture behave as its acceptance criteria describe."
    acceptance_criteria: Sequence[str] = ("The fixture's targeted tests pass.",)
    scope_paths: Sequence[str] = ()
    allow_test_file_changes: bool = True
    allowed_tools: Sequence[ToolName] = tuple(ToolName)
    sandbox_hook: SandboxHook | None = None
    fail_cleanup: bool = False


@dataclass(frozen=True, slots=True)
class E2ERun:
    scenario: E2EScenario
    completion: RuntimeCompletion
    result: PatchResult
    source_sha: str
    workspace_removed: bool
    artifacts: LocalArtifactStore = field(repr=False)

    @property
    def result_sha256(self) -> str:
        return canonical_sha256(self.result)

    @property
    def matches_expectation(self) -> bool:
        return (
            self.result.outcome is self.scenario.expected_outcome
            and self.result.failure is self.scenario.expected_failure
            and self.workspace_removed
        )


class ScriptedEngine:
    """A RuntimeEngine that replays a fixed script; hooks may inject workspace faults."""

    def __init__(self, steps: Sequence[ScriptStep], worktree: Path) -> None:
        self._steps = list(steps)
        self._worktree = worktree
        self.turns: list[RuntimeTurn] = []

    def next_action(self, turn: RuntimeTurn) -> RuntimeToolAction:
        self.turns.append(turn)
        while self._steps:
            step = self._steps.pop(0)
            if isinstance(step, RuntimeToolAction):
                return step
            if isinstance(step, Exception):
                raise step
            step(self._worktree)
        raise RuntimeError("Scripted engine has no further actions")


class _HookedSandbox:
    """Delegate to a FakeSandbox while letting a scenario change the tree during a run."""

    def __init__(self, inner: FakeSandbox, hook: SandboxHook | None) -> None:
        self.inner = inner
        self.hook = hook
        self.calls = 0

    def execute(self, request: SandboxRequest) -> SandboxExecution:
        self.calls += 1
        if self.hook is not None:
            self.hook(self.calls, request.workspace)
        return self.inner.execute(request)


class _FailingCleanupManager(WorkspaceManager):
    """Refuse the Runtime's cleanup once, so cleanup failure is exercised deterministically."""

    def cleanup(self, workspace_id: UUID) -> bool:
        raise WorkspaceError("Scenario fault: cleanup refused")

    def force_cleanup(self, workspace_id: UUID) -> bool:
        return super().cleanup(workspace_id)


def materialize_fixture(
    fixture: FixtureRepository, target: Path, git: GitRunner, committed_at: datetime
) -> str:
    """Create a one-commit repository with a fixed identity and date; return its SHA."""

    if committed_at.utcoffset() is None:
        raise ValueError("Fixture commit time must be timezone-aware")
    target.mkdir(parents=True)
    for relative, content in sorted(fixture.files.items()):
        path = target.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    timestamp = f"@{int(committed_at.timestamp())} +0000"
    identity = {
        "GIT_AUTHOR_NAME": FIXTURE_AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": FIXTURE_AUTHOR_EMAIL,
        "GIT_AUTHOR_DATE": timestamp,
        "GIT_COMMITTER_NAME": FIXTURE_AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": FIXTURE_AUTHOR_EMAIL,
        "GIT_COMMITTER_DATE": timestamp,
    }
    location = ["-C", str(target), "-c", "core.autocrlf=false"]
    git.run([*location, "init", "--quiet", "--initial-branch=main"])
    git.run([*location, "add", "--all", "--", "."])
    git.run(
        [*location, "commit", "--quiet", "--no-verify", "-m", fixture.commit_message],
        environment_overrides=identity,
    )
    return git.run([*location, "rev-parse", "HEAD"]).stdout_text().strip()


class PatchForgeE2EHarness:
    """Run scenarios through the real PatchForge path in a caller-owned directory."""

    def __init__(self, work_root: Path, *, now: datetime) -> None:
        if now.utcoffset() is None:
            raise ValueError("Harness time must be timezone-aware")
        self.work_root = work_root
        self.now = now

    def run(self, scenario: E2EScenario) -> E2ERun:
        root = self.work_root / scenario.name
        root.mkdir(parents=True)
        clock = self._clock
        git = GitRunner(root / "git-runtime")
        source_sha = materialize_fixture(scenario.fixture, root / "source", git, self.now)
        profile = scenario.profile
        profile_sha = canonical_sha256(profile)
        task = EngineeringTask(
            task_id=self._uuid(scenario, "task"),
            atlas_job_id=self._uuid(scenario, "atlas-job"),
            title=scenario.title,
            instructions=scenario.instructions,
            acceptance_criteria=list(scenario.acceptance_criteria),
            source=SourceRevision(
                repository_url=profile.repository_url,
                commit_sha=source_sha,
            ),
            repository_profile_id=profile.profile_id,
            repository_profile_sha256=profile_sha,
            scope_paths=list(scenario.scope_paths),
            created_at=self.now,
        )
        identity = RunIdentity(
            run_id=self._uuid(scenario, "run"),
            task_id=task.task_id,
            atlas_job_id=task.atlas_job_id,
            atlas_execution_id=self._uuid(scenario, "atlas-execution"),
            agent_id="patchforge.engineer",
            source=task.source,
            repository_profile_id=profile.profile_id,
            repository_profile_sha256=profile_sha,
            task_sha256=canonical_sha256(task),
            engine_kind="scripted",
            engine_version="e2e-v1",
            created_at=self.now,
        )
        policy = PatchForgePolicy(
            repository_profile_id=profile.profile_id,
            repository_profile_sha256=profile_sha,
            allowed_tools=list(scenario.allowed_tools),
            budgets=scenario.budgets,
            max_changed_files=20,
            max_diff_bytes=100_000,
            allow_test_file_changes=scenario.allow_test_file_changes,
        )
        manager_type = _FailingCleanupManager if scenario.fail_cleanup else WorkspaceManager
        manager = manager_type(root / "workspaces", clock=clock, git_runner=git)
        handle = manager.provision(
            task,
            profile,
            identity.run_id,
            root / "source",
            lease_duration=timedelta(minutes=5),
        )
        sandbox = _HookedSandbox(
            FakeSandbox(
                scenario.sandbox_plans,
                clock=clock,
                id_factory=self._counter(scenario, "execution"),
            ),
            scenario.sandbox_hook,
        )
        gateway = ToolGateway(
            identity=identity,
            task=task,
            profile=profile,
            policy=policy,
            workspace=handle,
            workspace_manager=manager,
            sandbox=sandbox,
            clock=clock,
            id_factory=self._counter(scenario, "tool-call"),
        )
        runtime = PatchForgeRuntime(
            gateway=gateway,
            workspace_manager=manager,
            engine=ScriptedEngine(scenario.steps, handle.worktree),
            lease_duration=timedelta(minutes=5),
            clock=clock,
        )
        completion = runtime.execute()
        workspace_removed = not handle.root.exists()
        if isinstance(manager, _FailingCleanupManager):
            # The Runtime's cleanup was refused on purpose; recorded evidence already says
            # so. Remove the workspace now so the harness never leaks a tree.
            manager.force_cleanup(handle.record.workspace_id)
            workspace_removed = not handle.root.exists()
        artifacts = LocalArtifactStore(root / "artifacts")
        attestor = PatchForgeAttestor(
            task=task, profile=profile, policy=policy, artifact_store=artifacts
        )
        return E2ERun(
            scenario=scenario,
            completion=completion,
            result=attestor.attest(completion),
            source_sha=source_sha,
            workspace_removed=workspace_removed,
            artifacts=artifacts,
        )

    def _clock(self) -> datetime:
        return self.now

    @staticmethod
    def _uuid(scenario: E2EScenario, purpose: str) -> UUID:
        return uuid5(_E2E_NAMESPACE, f"{scenario.name}:{purpose}")

    @staticmethod
    def _counter(scenario: E2EScenario, purpose: str) -> Callable[[], UUID]:
        count = 0

        def next_id() -> UUID:
            nonlocal count
            count += 1
            return uuid5(_E2E_NAMESPACE, f"{scenario.name}:{purpose}:{count}")

        return next_id


__all__ = [
    "E2ERun",
    "E2EScenario",
    "FixtureRepository",
    "PatchForgeE2EHarness",
    "SandboxHook",
    "ScriptStep",
    "ScriptedEngine",
    "WorkspaceHook",
    "materialize_fixture",
]
