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
    PatchForgePhase,
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
    RuntimeEngine,
    RuntimeToolAction,
    RuntimeTurn,
)
from nexus.patchforge.sandbox import (
    FakeSandbox,
    FakeSandboxPlan,
    SandboxExecution,
    SandboxExecutor,
    SandboxRequest,
)
from nexus.patchforge.workspace import (
    GitRunner,
    WorkspaceError,
    WorkspaceHandle,
    WorkspaceManager,
)

_E2E_NAMESPACE = UUID("8d3c2f64-0b1e-4c55-9a27-2f6e1d0c9b31")
FIXTURE_AUTHOR_NAME = "PatchForge Fixture"
FIXTURE_AUTHOR_EMAIL = "patchforge-fixture@nexus.invalid"

WorkspaceHook = Callable[[Path], None]
"""Fault injection that changes the worktree outside any tool call."""

SandboxHook = Callable[[int, Path], None]
"""Fault injection run inside the Nth sandbox execution, before its planned result."""

ScriptStep = RuntimeToolAction | Exception | WorkspaceHook

SandboxFactory = Callable[
    [RepositoryProfile, Callable[[], datetime], Callable[[], UUID]], "SandboxExecutor"
]
"""Build a scenario-specific executor from the profile, clock, and execution-ID factory."""


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
    fail_lease_renewal_at: int | None = None
    sandbox_factory: SandboxFactory | None = None
    engine_factory: Callable[[Path], RuntimeEngine] | None = None
    """Replace the scripted engine (for example with a model-backed one); gets the worktree."""
    engine_kind: str = "scripted"
    engine_version: str = "e2e-v1"
    expected_phase_reached: PatchForgePhase = PatchForgePhase.CLOSED
    expected_implementation_loops: int = 0
    expected_findings: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class E2ERun:
    scenario: E2EScenario
    completion: RuntimeCompletion
    result: PatchResult
    source_sha: str
    root: Path
    workspace_removed: bool
    source_refs_after: str
    source_refs_before: str
    sandbox_requests: Sequence[SandboxRequest]
    task: EngineeringTask
    artifacts: LocalArtifactStore = field(repr=False)

    @property
    def result_sha256(self) -> str:
        return canonical_sha256(self.result)

    @property
    def matches_expectation(self) -> bool:
        return not self.problems()

    def problems(self) -> list[str]:
        """Every expectation and universal PatchForge invariant this run violates."""

        scenario = self.scenario
        result = self.result
        snapshot = self.completion.snapshot
        problems: list[str] = []

        def check(condition: bool, message: str) -> None:
            if not condition:
                problems.append(message)

        check(result.outcome is scenario.expected_outcome, "unexpected outcome")
        check(result.failure is scenario.expected_failure, "unexpected failure")
        check(result.phase_reached is scenario.expected_phase_reached, "unexpected phase")
        check(
            snapshot.implementation_loops == scenario.expected_implementation_loops,
            "unexpected implementation-loop count",
        )
        codes = {item.code for item in result.policy_findings}
        check(set(scenario.expected_findings) <= codes, "expected finding missing")
        return problems + self.invariant_problems()

    def invariant_problems(self) -> list[str]:
        """Universal PatchForge invariants that must hold for every run, whatever its outcome."""

        result = self.result
        snapshot = self.completion.snapshot
        problems: list[str] = []

        def check(condition: bool, message: str) -> None:
            if not condition:
                problems.append(message)

        # Lifecycle: a closed transcript that always ends by cleaning up.
        transitions = snapshot.transitions
        check(transitions[0].source is PatchForgePhase.CREATED, "transcript start")
        check(snapshot.phase is PatchForgePhase.CLOSED, "lifecycle not closed")
        check(
            any(item.target is PatchForgePhase.CLEANUP for item in transitions),
            "cleanup phase never entered",
        )
        check(self.workspace_removed, "workspace was not removed")
        # Evidence authority: every claim resolves to runtime-owned records.
        calls = {item.call_id for item in result.tool_calls}
        check(
            all(item.tool_call_id in calls for item in result.executions),
            "execution without a tool call",
        )
        check(
            result.budget_usage.total_tool_calls == len(result.tool_calls),
            "budget usage does not match the tool-call ledger",
        )
        check(
            all(item.attested_by == "patchforge.runtime" for item in result.tool_calls),
            "tool call not runtime-attested",
        )
        if result.outcome is PatchOutcome.PATCH_PROPOSED:
            check(result.diff is not None, "proposal without a diff")
        # No approval, merge, deployment, or push: the source repository is untouched.
        check(self.source_refs_after == self.source_refs_before, "source repository changed")
        # No network, secrets, memory, parallel calls, or live model.
        check(
            all(
                item.policy.network_disabled and not item.policy.secrets_allowed
                for item in self.sandbox_requests
            ),
            "sandbox request allowed network or secrets",
        )
        identity = result.identity
        check(identity.memory_mode == "disabled", "memory enabled")
        check(identity.parallel_tool_calls is False, "parallel tool calls enabled")
        check(
            identity.engine_kind == self.scenario.engine_kind,
            "engine identity differs from the scenario",
        )
        return problems


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
    """Record every request and let a scenario change the tree during a run."""

    def __init__(self, inner: SandboxExecutor, hook: SandboxHook | None) -> None:
        self.inner = inner
        self.hook = hook
        self.calls = 0
        self.requests: list[SandboxRequest] = []

    def execute(self, request: SandboxRequest) -> SandboxExecution:
        self.calls += 1
        self.requests.append(request)
        if self.hook is not None:
            self.hook(self.calls, request.workspace)
        return self.inner.execute(request)


class _FaultyWorkspaceManager(WorkspaceManager):
    """Inject scenario faults: a refused cleanup or a failed Nth lease renewal."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime],
        git_runner: GitRunner,
        fail_cleanup: bool,
        fail_lease_renewal_at: int | None,
    ) -> None:
        super().__init__(root, clock=clock, git_runner=git_runner)
        self.fail_cleanup = fail_cleanup
        self.fail_lease_renewal_at = fail_lease_renewal_at
        self.renewals = 0

    def renew_lease(self, handle: WorkspaceHandle, lease_duration: timedelta) -> WorkspaceHandle:
        self.renewals += 1
        if self.renewals == self.fail_lease_renewal_at:
            raise WorkspaceError("Scenario fault: lease renewal refused")
        return super().renew_lease(handle, lease_duration)

    def cleanup(self, workspace_id: UUID) -> bool:
        if self.fail_cleanup:
            raise WorkspaceError("Scenario fault: cleanup refused")
        return super().cleanup(workspace_id)

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
            engine_kind=scenario.engine_kind,
            engine_version=scenario.engine_version,
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
        manager = _FaultyWorkspaceManager(
            root / "workspaces",
            clock=clock,
            git_runner=git,
            fail_cleanup=scenario.fail_cleanup,
            fail_lease_renewal_at=scenario.fail_lease_renewal_at,
        )
        source_refs_before = self._source_refs(git, root / "source")
        handle = manager.provision(
            task,
            profile,
            identity.run_id,
            root / "source",
            lease_duration=timedelta(minutes=5),
        )
        execution_ids = self._counter(scenario, "execution")
        inner: SandboxExecutor = (
            scenario.sandbox_factory(profile, clock, execution_ids)
            if scenario.sandbox_factory is not None
            else FakeSandbox(scenario.sandbox_plans, clock=clock, id_factory=execution_ids)
        )
        sandbox = _HookedSandbox(inner, scenario.sandbox_hook)
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
            engine=(
                scenario.engine_factory(handle.worktree)
                if scenario.engine_factory is not None
                else ScriptedEngine(scenario.steps, handle.worktree)
            ),
            lease_duration=timedelta(minutes=5),
            clock=clock,
        )
        completion = runtime.execute()
        workspace_removed = not handle.root.exists()
        if scenario.fail_cleanup:
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
            root=root,
            workspace_removed=workspace_removed,
            source_refs_before=source_refs_before,
            source_refs_after=self._source_refs(git, root / "source"),
            sandbox_requests=tuple(sandbox.requests),
            task=task,
            artifacts=artifacts,
        )

    def _clock(self) -> datetime:
        return self.now

    @staticmethod
    def _source_refs(git: GitRunner, source: Path) -> str:
        """All refs and their targets in the operator's source repository."""

        return git.run(["-C", str(source), "show-ref", "--head"]).stdout_text()

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
