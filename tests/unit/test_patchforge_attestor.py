"""PatchForge Milestone F attestation over the real Runtime, gateway, and workspace path."""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from nexus.atlas.models import SourceRevision
from nexus.patchforge.attestor import (
    PATCH_ARTIFACT_TYPE,
    ArtifactStoreError,
    LocalArtifactStore,
    PatchForgeAttestor,
)
from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.gateway import (
    AdvancePhaseArguments,
    CreateFileArguments,
    ListTreeArguments,
    SubmitReportArguments,
    ToolGateway,
    WritePatchArguments,
)
from nexus.patchforge.models import (
    AgentReport,
    CheckKind,
    CheckStatus,
    EngineeringTask,
    FindingSeverity,
    PatchForgeFailure,
    PatchForgePhase,
    PatchOutcome,
    PatchResult,
    PhaseBudget,
    ReproductionStatus,
    RunBudgets,
    RunIdentity,
    ToolName,
)
from nexus.patchforge.policy import (
    CommandPurpose,
    PatchForgePolicy,
    RepositoryProfile,
    SandboxCommand,
    SandboxPolicy,
)
from nexus.patchforge.runtime import PatchForgeRuntime, RuntimeCompletion, RuntimeToolAction
from nexus.patchforge.sandbox import (
    FakeSandbox,
    FakeSandboxPlan,
    SandboxExecution,
    SandboxRequest,
    SandboxStatus,
)
from nexus.patchforge.workspace import WorkspaceManager

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
ORIGINAL = "def subtract(a, b):\n    return a + b\n"
FIXED = "def subtract(a, b):\n    return a - b\n"
Step = RuntimeToolAction | Exception | Callable[[Path], None]


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        shell=False,
    )
    return result.stdout.decode("utf-8").strip()


def _repository(path: Path) -> tuple[Path, str]:
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(path)],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        shell=False,
    )
    _git(path, "config", "user.name", "Attestor Fixture")
    _git(path, "config", "user.email", "attestor@example.invalid")
    (path / "calculator.py").write_text(ORIGINAL, encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_calculator.py").write_text(
        "from calculator import subtract\n\ndef test_subtract():\n    assert subtract(5, 2) == 3\n",
        encoding="utf-8",
    )
    _git(path, "add", ".")
    _git(path, "commit", "-m", "fixture: broken subtraction")
    return path, _git(path, "rev-parse", "HEAD")


def _profile(purposes: set[CommandPurpose] | None = None) -> RepositoryProfile:
    selected = set(CommandPurpose) if purposes is None else purposes
    return RepositoryProfile(
        profile_id="fixture.python",
        profile_version=1,
        repository_url="https://example.invalid/repository",
        sandbox=SandboxPolicy(
            image=f"sha256:{'d' * 64}",
            run_as_user="10001:10001",
            cpu_limit_millis=1000,
            memory_limit_mb=256,
            pids_limit=64,
            default_timeout_seconds=60,
            max_output_bytes=100_000,
        ),
        commands={
            purpose: SandboxCommand(
                executable="python",
                arguments=["-m", "pytest", purpose.value],
                timeout_seconds=30,
                max_output_bytes=100_000,
            )
            for purpose in selected
        },
        protected_paths=["protected.txt"],
        test_path_prefixes=["tests"],
    )


def _budgets() -> RunBudgets:
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
        max_implementation_loops=1,
        max_total_tool_calls=92,
        max_total_duration_seconds=600,
    )


def _plan(profile: RepositoryProfile, purpose: CommandPurpose, *, passed: bool) -> FakeSandboxPlan:
    return FakeSandboxPlan(
        expected_command_sha256=canonical_sha256(profile.commands[purpose]),
        status=SandboxStatus.SUCCEEDED if passed else SandboxStatus.FAILED,
        exit_code=0 if passed else 1,
        stdout=b"passed\n" if passed else b"failed\n",
        error_code=None if passed else "command_failed",
    )


def _advance(target: PatchForgePhase) -> RuntimeToolAction:
    return RuntimeToolAction(
        tool_name=ToolName.ADVANCE_PHASE,
        arguments=AdvancePhaseArguments(target_phase=target),
    )


def _run_tests() -> RuntimeToolAction:
    return RuntimeToolAction(tool_name=ToolName.RUN_TARGETED_TESTS)


def _write(content: str, previous: str) -> RuntimeToolAction:
    return RuntimeToolAction(
        tool_name=ToolName.WRITE_PATCH,
        arguments=WritePatchArguments(
            path="calculator.py",
            expected_sha256=sha256(previous.encode("utf-8")).hexdigest(),
            content=content,
        ),
    )


def _report() -> RuntimeToolAction:
    return RuntimeToolAction(
        tool_name=ToolName.SUBMIT_REPORT,
        arguments=SubmitReportArguments(
            report=AgentReport(
                summary="Fixed subtraction.",
                hypothesis="Subtraction added its operands.",
                implementation="Replaced the operator.",
            )
        ),
    )


def _happy_steps() -> list[Step]:
    return [
        RuntimeToolAction(tool_name=ToolName.LIST_TREE, arguments=ListTreeArguments()),
        _advance(PatchForgePhase.HYPOTHESIS),
        _advance(PatchForgePhase.REPRODUCE),
        _run_tests(),
        _advance(PatchForgePhase.IMPLEMENT),
        _write(FIXED, ORIGINAL),
        _advance(PatchForgePhase.TARGETED_VALIDATE),
        _run_tests(),
        _advance(PatchForgePhase.FULL_VALIDATE),
        RuntimeToolAction(tool_name=ToolName.RUN_TEST_SUITE),
        _advance(PatchForgePhase.SELF_REVIEW),
        _advance(PatchForgePhase.FINALIZE),
        _report(),
    ]


def _happy_plans(profile: RepositoryProfile) -> list[FakeSandboxPlan]:
    return [
        _plan(profile, CommandPurpose.REPRODUCTION, passed=False),
        _plan(profile, CommandPurpose.TARGETED_TESTS, passed=True),
        _plan(profile, CommandPurpose.FULL_TEST_SUITE, passed=True),
    ]


class _Engine:
    """Scripted engine; callable steps mutate the worktree out of band, then continue."""

    def __init__(self, steps: list[Step], worktree: Path) -> None:
        self.steps = list(steps)
        self.worktree = worktree

    def next_action(self, turn: object) -> RuntimeToolAction:
        while self.steps:
            step = self.steps.pop(0)
            if isinstance(step, Exception):
                raise step
            if isinstance(step, RuntimeToolAction):
                return step
            step(self.worktree)
        raise AssertionError("Scripted engine exhausted")


class _MutatingSandbox:
    """Delegate to FakeSandbox, but let a hook change the worktree during chosen runs."""

    def __init__(self, inner: FakeSandbox, hook: Callable[[int, Path], None] | None) -> None:
        self.inner = inner
        self.hook = hook
        self.calls = 0

    def execute(self, request: SandboxRequest) -> SandboxExecution:
        self.calls += 1
        if self.hook is not None:
            self.hook(self.calls, request.workspace)
        return self.inner.execute(request)


@dataclass
class _Run:
    completion: RuntimeCompletion
    attestor: PatchForgeAttestor
    store: LocalArtifactStore
    source_sha: str


def _run(
    tmp_path: Path,
    steps: list[Step],
    *,
    plans: list[FakeSandboxPlan] | None = None,
    profile: RepositoryProfile | None = None,
    scope_paths: list[str] | None = None,
    sandbox_hook: Callable[[int, Path], None] | None = None,
) -> _Run:
    source, source_sha = _repository(tmp_path / "source")
    selected = profile or _profile()
    task = EngineeringTask(
        task_id=UUID(int=2),
        atlas_job_id=UUID(int=3),
        title="Fix subtraction",
        instructions="Make subtraction subtract.",
        acceptance_criteria=["subtract(5, 2) == 3"],
        source=SourceRevision(
            repository_url="https://example.invalid/repository",
            commit_sha=source_sha,
        ),
        repository_profile_id=selected.profile_id,
        repository_profile_sha256=canonical_sha256(selected),
        scope_paths=scope_paths or [],
        created_at=NOW,
    )
    identity = RunIdentity(
        run_id=UUID(int=10),
        task_id=task.task_id,
        atlas_job_id=task.atlas_job_id,
        atlas_execution_id=UUID(int=4),
        agent_id="patchforge.engineer",
        source=task.source,
        repository_profile_id=selected.profile_id,
        repository_profile_sha256=canonical_sha256(selected),
        task_sha256=canonical_sha256(task),
        engine_kind="scripted",
        engine_version="v1",
        created_at=NOW,
    )
    policy = PatchForgePolicy(
        repository_profile_id=selected.profile_id,
        repository_profile_sha256=canonical_sha256(selected),
        allowed_tools=list(ToolName),
        budgets=_budgets(),
        max_changed_files=20,
        max_diff_bytes=100_000,
        allow_test_file_changes=True,
    )
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    handle = manager.provision(
        task, selected, identity.run_id, source, lease_duration=timedelta(minutes=5)
    )
    sandbox = _MutatingSandbox(
        FakeSandbox(
            _happy_plans(selected) if plans is None else plans,
            clock=lambda: NOW,
        ),
        sandbox_hook,
    )
    gateway = ToolGateway(
        identity=identity,
        task=task,
        profile=selected,
        policy=policy,
        workspace=handle,
        workspace_manager=manager,
        sandbox=sandbox,
        clock=lambda: NOW,
    )
    runtime = PatchForgeRuntime(
        gateway=gateway,
        workspace_manager=manager,
        engine=_Engine(steps, handle.worktree),
        lease_duration=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    completion = runtime.execute()
    assert not handle.root.exists()
    store = LocalArtifactStore(tmp_path / "artifacts")
    attestor = PatchForgeAttestor(task=task, profile=selected, policy=policy, artifact_store=store)
    return _Run(completion, attestor, store, source_sha)


def _codes(result: PatchResult) -> dict[str, FindingSeverity]:
    return {item.code: item.severity for item in result.policy_findings}


def test_clean_run_is_attested_as_a_patch_proposal(tmp_path: Path) -> None:
    run = _run(tmp_path, _happy_steps())

    result = run.attestor.attest(run.completion)

    assert result.outcome is PatchOutcome.PATCH_PROPOSED
    assert result.failure is None
    assert result.phase_reached is PatchForgePhase.CLOSED
    assert result.reproduction is not None
    assert result.reproduction.status is ReproductionStatus.FAIL_BEFORE_PASS_AFTER
    assert {item.check_kind: item.status for item in result.checks} == {
        CheckKind.TARGETED_TESTS: CheckStatus.PASSED,
        CheckKind.FULL_TEST_SUITE: CheckStatus.PASSED,
    }
    diff = result.diff
    assert diff is not None and diff.base_sha == run.source_sha
    assert diff.changed_files == ["calculator.py"]
    assert diff.proposed_head_sha == run.completion.final_capture.proposed_head_sha  # type: ignore[union-attr]
    patch = run.store.read(diff.patch_artifact)
    assert b"return a - b" in patch and sha256(patch).hexdigest() == diff.diff_sha256
    assert diff.patch_artifact.artifact_type == PATCH_ARTIFACT_TYPE
    assert not any(item.severity is FindingSeverity.BLOCKING for item in result.policy_findings)
    assert _codes(result) == {
        "optional_check_not_run": FindingSeverity.INFO,
    }
    atlas = result.to_atlas_result()
    assert atlas.proposed_head_sha == diff.proposed_head_sha
    assert run.attestor.attest(run.completion) == result


def test_validation_before_a_later_change_does_not_count(tmp_path: Path) -> None:
    revised = "def subtract(a, b):\n    return a - b  # revised\n"
    steps: list[Step] = [
        *_happy_steps()[:8],
        _advance(PatchForgePhase.IMPLEMENT),
        _write(revised, FIXED),
        _advance(PatchForgePhase.TARGETED_VALIDATE),
        *_happy_steps()[8:],
    ]
    run = _run(tmp_path, steps)

    result = run.attestor.attest(run.completion)

    assert result.outcome is PatchOutcome.PARTIAL
    assert result.failure is PatchForgeFailure.VALIDATION_FAILED
    checks = {item.check_kind: item.status for item in result.checks}
    assert checks[CheckKind.TARGETED_TESTS] is CheckStatus.NOT_RUN
    assert checks[CheckKind.FULL_TEST_SUITE] is CheckStatus.PASSED
    assert result.reproduction is not None
    assert result.reproduction.status is ReproductionStatus.FAIL_BEFORE_NO_PASS
    assert _codes(result)["stale_validation"] is FindingSeverity.WARNING


@pytest.mark.parametrize(
    ("position", "code"),
    [
        (9, "workspace_changed_out_of_band"),
        (11, "final_state_unobserved"),
    ],
)
def test_out_of_band_workspace_change_is_attestation_failure(
    tmp_path: Path, position: int, code: str
) -> None:
    def tamper(worktree: Path) -> None:
        (worktree / "calculator.py").write_text(FIXED + "# injected\n", encoding="utf-8")

    steps = _happy_steps()
    steps.insert(position, tamper)
    run = _run(tmp_path, steps)

    result = run.attestor.attest(run.completion)

    assert result.outcome is PatchOutcome.ABORTED
    assert result.failure is PatchForgeFailure.ATTESTATION_FAILED
    assert _codes(result)[code] is FindingSeverity.BLOCKING
    with pytest.raises(ValueError, match="Only patch-proposed"):
        result.to_atlas_result()


def test_validation_that_changes_the_tree_does_not_count(tmp_path: Path) -> None:
    def rewrite_during_full_suite(call: int, worktree: Path) -> None:
        if call == 3:
            (worktree / "generated.txt").write_text("side effect\n", encoding="utf-8")

    run = _run(tmp_path, _happy_steps(), sandbox_hook=rewrite_during_full_suite)

    result = run.attestor.attest(run.completion)

    assert result.outcome is PatchOutcome.PARTIAL
    checks = {item.check_kind: item.status for item in result.checks}
    assert checks[CheckKind.FULL_TEST_SUITE] is CheckStatus.NOT_RUN
    assert checks[CheckKind.TARGETED_TESTS] is CheckStatus.NOT_RUN
    assert result.diff is not None and "generated.txt" in result.diff.changed_files


def test_missing_reproduction_blocks_the_proposal(tmp_path: Path) -> None:
    steps = [step for index, step in enumerate(_happy_steps()) if index != 3]
    profile = _profile()
    run = _run(tmp_path, steps, plans=_happy_plans(profile)[1:], profile=profile)

    result = run.attestor.attest(run.completion)

    assert result.outcome is PatchOutcome.PARTIAL
    assert result.failure is PatchForgeFailure.VALIDATION_FAILED
    assert result.reproduction is None
    assert _codes(result)["reproduction_missing"] is FindingSeverity.BLOCKING


def test_reproduction_that_already_passes_is_a_warning(tmp_path: Path) -> None:
    profile = _profile()
    plans = _happy_plans(profile)
    plans[0] = _plan(profile, CommandPurpose.REPRODUCTION, passed=True)
    run = _run(tmp_path, _happy_steps(), plans=plans, profile=profile)

    result = run.attestor.attest(run.completion)

    assert result.outcome is PatchOutcome.PATCH_PROPOSED
    assert result.reproduction is not None
    assert result.reproduction.status is ReproductionStatus.PASS_BEFORE
    assert _codes(result)["reproduction_not_demonstrated"] is FindingSeverity.WARNING


def test_profile_without_reproduction_command_is_not_practical(tmp_path: Path) -> None:
    profile = _profile({CommandPurpose.TARGETED_TESTS, CommandPurpose.FULL_TEST_SUITE})
    steps = [step for index, step in enumerate(_happy_steps()) if index != 3]
    plans = [
        _plan(profile, CommandPurpose.TARGETED_TESTS, passed=True),
        _plan(profile, CommandPurpose.FULL_TEST_SUITE, passed=True),
    ]
    run = _run(tmp_path, steps, plans=plans, profile=profile)

    result = run.attestor.attest(run.completion)

    assert result.outcome is PatchOutcome.PATCH_PROPOSED
    assert result.reproduction is not None
    assert result.reproduction.status is ReproductionStatus.NOT_PRACTICAL
    assert "optional_check_not_run" not in _codes(result)


def test_out_of_scope_change_is_a_policy_violation(tmp_path: Path) -> None:
    steps = _happy_steps()
    steps.insert(
        6,
        RuntimeToolAction(
            tool_name=ToolName.CREATE_FILE,
            arguments=CreateFileArguments(path="notes.md", content="notes\n"),
        ),
    )
    run = _run(tmp_path, steps, scope_paths=["calculator.py", "notes.md"])
    in_scope = run.attestor.attest(run.completion)
    assert in_scope.outcome is PatchOutcome.PATCH_PROPOSED

    # The gateway refuses out-of-scope writes, so the attestor's check is defense in depth
    # for changes made by code running in the sandbox.
    def write_outside_scope(call: int, worktree: Path) -> None:
        if call == 1:
            (worktree / "extra.txt").write_text("created by the reproduction\n")

    scoped = _run(
        tmp_path / "scoped",
        _happy_steps(),
        scope_paths=["calculator.py"],
        sandbox_hook=write_outside_scope,
    )
    result = scoped.attestor.attest(scoped.completion)

    assert result.outcome is PatchOutcome.POLICY_VIOLATION
    assert result.failure is PatchForgeFailure.POLICY_DENIED
    assert result.diff is not None and result.diff.scope_violations == ["extra.txt"]
    assert _codes(result)["scope_violation"] is FindingSeverity.BLOCKING


def test_reported_run_without_changes_is_partial(tmp_path: Path) -> None:
    steps = [step for index, step in enumerate(_happy_steps()) if index != 5]
    run = _run(tmp_path, steps)

    result = run.attestor.attest(run.completion)

    assert result.outcome is PatchOutcome.PARTIAL
    assert result.diff is None
    assert _codes(result)["no_changes"] is FindingSeverity.BLOCKING


def test_command_hash_tampering_is_detected(tmp_path: Path) -> None:
    run = _run(tmp_path, _happy_steps())
    executions = list(run.completion.executions)
    executions[2] = executions[2].model_copy(update={"command_sha256": "0" * 64})
    tampered = run.completion.model_copy(update={"executions": executions})

    result = run.attestor.attest(tampered)

    assert result.outcome is PatchOutcome.ABORTED
    assert result.failure is PatchForgeFailure.ATTESTATION_FAILED
    assert _codes(result)["command_hash_mismatch"] is FindingSeverity.BLOCKING
    assert len(result.executions) == 2


def test_runtime_failure_keeps_its_classification(tmp_path: Path) -> None:
    steps: list[Step] = [*_happy_steps()[:4], RuntimeError("engine crashed")]
    steps += [_advance(PatchForgePhase.REPORTED)]
    run = _run(tmp_path, steps, plans=_happy_plans(_profile())[:1])

    result = run.attestor.attest(run.completion)

    assert result.failure is PatchForgeFailure.ENGINE_ERROR
    assert result.outcome is PatchOutcome.ABORTED
    assert result.phase_reached is PatchForgePhase.REPRODUCE
    assert result.report.summary.startswith("Runtime placeholder")
    assert len(result.executions) == 1


def test_attestor_rejects_mismatched_bindings(tmp_path: Path) -> None:
    run = _run(tmp_path, _happy_steps())
    other = run.attestor.task.model_copy(update={"title": "Another task"})
    with pytest.raises(ValueError, match="another task"):
        PatchForgeAttestor(
            task=other,
            profile=run.attestor.profile,
            policy=run.attestor.policy,
            artifact_store=run.store,
        ).attest(run.completion)
    with pytest.raises(ValueError, match="profile"):
        PatchForgeAttestor(
            task=run.attestor.task,
            profile=_profile({CommandPurpose.TARGETED_TESTS}),
            policy=run.attestor.policy,
            artifact_store=run.store,
        )


def test_local_artifact_store_is_content_addressed_and_verified(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    first = store.put(b"patch", artifact_type=PATCH_ARTIFACT_TYPE)
    assert store.put(b"patch", artifact_type=PATCH_ARTIFACT_TYPE) == first
    assert first.uri == f"nexus-artifact:sha256:{sha256(b'patch').hexdigest()}"
    assert store.read(first) == b"patch"
    (store.root / f"{first.sha256}.{PATCH_ARTIFACT_TYPE}").write_bytes(b"tampered")
    with pytest.raises(ArtifactStoreError):
        store.read(first)
    with pytest.raises(ArtifactStoreError):
        store.put(b"patch", artifact_type=PATCH_ARTIFACT_TYPE)


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("protected.txt", "protected_path_changed"),
        ("config/credentials.json", "sensitive_path_changed"),
    ],
)
def test_sandbox_created_protected_or_sensitive_files_block_the_patch(
    tmp_path: Path, path: str, code: str
) -> None:
    def create(call: int, worktree: Path) -> None:
        if call == 1:
            target = worktree / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("written in the sandbox\n", encoding="utf-8")

    run = _run(tmp_path, _happy_steps(), sandbox_hook=create)

    result = run.attestor.attest(run.completion)

    assert result.outcome is PatchOutcome.POLICY_VIOLATION
    assert _codes(result)[code] is FindingSeverity.BLOCKING
    assert result.diff is not None and path in result.diff.changed_files
