"""Deterministic PatchForge Runtime coordinator tests."""

import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from nexus.atlas.models import SourceRevision
from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.gateway import (
    AdvancePhaseArguments,
    FailureOutput,
    GatewayRequestError,
    GatewayResult,
    GitDiffArguments,
    ListTreeArguments,
    PhaseRequestOutput,
    ReadFileRangeArguments,
    ReportOutput,
    StatusOutput,
    SubmitReportArguments,
    ToolGateway,
    WritePatchArguments,
)
from nexus.patchforge.models import (
    AgentReport,
    EngineeringTask,
    PatchForgeFailure,
    PatchForgePhase,
    PatchOutcome,
    PhaseBudget,
    PhaseUsage,
    RunBudgets,
    RunBudgetUsage,
    RunIdentity,
    ToolCallRecord,
    ToolCallStatus,
    ToolName,
)
from nexus.patchforge.policy import (
    CommandPurpose,
    PatchForgePolicy,
    RepositoryProfile,
    SandboxCommand,
    SandboxPolicy,
)
from nexus.patchforge.runtime import (
    PatchForgeRuntime,
    RuntimeCancelledError,
    RuntimeToolAction,
    RuntimeTurn,
)
from nexus.patchforge.sandbox import FakeSandbox, FakeSandboxPlan, SandboxStatus
from nexus.patchforge.workspace import (
    WorkspaceError,
    WorkspaceHandle,
    WorkspaceManager,
    WorkspaceRecord,
    WorkspaceState,
)

NOW = datetime(2026, 9, 28, 9, tzinfo=UTC)


def _phase_budget(*, calls: int = 10) -> PhaseBudget:
    return PhaseBudget(
        max_tool_calls=calls,
        max_duration_seconds=60,
        max_output_bytes=100_000,
    )


def _budgets(*, loops: int = 1) -> RunBudgets:
    budget = _phase_budget()
    return RunBudgets(
        provisioning=budget,
        recon=budget,
        hypothesis=budget,
        reproduce=budget,
        implement=budget,
        targeted_validate=budget,
        full_validate=budget,
        self_review=budget,
        finalization_reserve=_phase_budget(calls=2),
        cleanup=budget,
        max_implementation_loops=loops,
        max_total_tool_calls=92,
        max_total_duration_seconds=600,
    )


def _task() -> EngineeringTask:
    return EngineeringTask(
        task_id=UUID(int=2),
        atlas_job_id=UUID(int=3),
        title="Repair deterministic fixture",
        instructions="Make the bounded fixture correct.",
        acceptance_criteria=["The fixture behavior is correct."],
        source=SourceRevision(
            repository_url="https://example.invalid/repository",
            commit_sha="a" * 40,
        ),
        repository_profile_id="fixture.python",
        repository_profile_sha256="b" * 64,
        created_at=NOW,
    )


def _identity(task: EngineeringTask) -> RunIdentity:
    return RunIdentity(
        run_id=UUID(int=1),
        task_id=task.task_id,
        atlas_job_id=task.atlas_job_id,
        atlas_execution_id=UUID(int=4),
        agent_id="patchforge.engineer",
        source=task.source,
        repository_profile_id=task.repository_profile_id,
        repository_profile_sha256=task.repository_profile_sha256,
        task_sha256=canonical_sha256(task),
        engine_kind="scripted",
        engine_version="v1",
        created_at=NOW,
    )


def _workspace() -> WorkspaceHandle:
    return WorkspaceHandle(
        record=WorkspaceRecord(
            workspace_id=UUID(int=1),
            run_id=UUID(int=1),
            task_id=UUID(int=2),
            source_sha="a" * 40,
            branch_name="nexus/patchforge/fixture",
            state=WorkspaceState.ACTIVE,
            created_at=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        ),
        root=Path("runtime-fixture"),
        worktree=Path("runtime-fixture/worktree"),
        git_directory=Path("runtime-fixture/control/repository.git"),
    )


class ScriptedEngine:
    def __init__(self, steps: list[RuntimeToolAction | Exception]) -> None:
        self.steps = list(steps)
        self.turns: list[RuntimeTurn] = []

    def next_action(self, turn: RuntimeTurn) -> RuntimeToolAction:
        self.turns.append(turn)
        if not self.steps:
            raise AssertionError("Scripted engine exhausted")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class FakeWorkspaceManager:
    def __init__(self, events: list[str], *, cleanup_error: bool = False) -> None:
        self.events = events
        self.cleanup_error = cleanup_error
        self.renewals = 0
        self.cleanup_calls = 0

    def renew_lease(
        self,
        handle: WorkspaceHandle,
        lease_duration: timedelta,
    ) -> WorkspaceHandle:
        self.events.append("renew")
        self.renewals += 1
        record = handle.record.model_copy(
            update={"lease_expires_at": handle.record.lease_expires_at + lease_duration}
        )
        return handle.model_copy(update={"record": record})

    def cleanup(self, workspace_id: UUID) -> bool:
        assert workspace_id == UUID(int=1)
        self.events.append("cleanup")
        self.cleanup_calls += 1
        if self.cleanup_error:
            raise WorkspaceError("fixture cleanup failed")
        return True


class FakeGateway:
    _WORKSPACE_FREE = frozenset(
        {ToolName.ADVANCE_PHASE, ToolName.SUBMIT_REPORT, ToolName.INSPECT_TEST_FAILURE}
    )

    def __init__(
        self,
        *,
        events: list[str],
        workspace_manager: FakeWorkspaceManager,
        budgets: RunBudgets | None = None,
        failure_code: str | None = None,
    ) -> None:
        self.task = _task()
        self.identity = _identity(self.task)
        self.policy = PatchForgePolicy(
            repository_profile_id=self.task.repository_profile_id,
            repository_profile_sha256=self.task.repository_profile_sha256,
            allowed_tools=list(ToolName),
            budgets=budgets or _budgets(),
            max_changed_files=20,
            max_diff_bytes=100_000,
            allow_test_file_changes=True,
        )
        self.workspace = _workspace()
        self.workspace_manager = workspace_manager
        self.events = events
        self.failure_code = failure_code
        self.refresh_calls = 0
        self._records: list[ToolCallRecord] = []

    @property
    def records(self) -> tuple[ToolCallRecord, ...]:
        return tuple(self._records)

    @property
    def executions(self) -> tuple[()]:
        return ()

    @property
    def budget_usage(self) -> RunBudgetUsage:
        phases = [
            PhaseUsage(
                phase=phase,
                tool_calls=sum(record.phase is phase for record in self._records),
                duration_seconds=0,
                output_bytes=sum(
                    record.output_bytes for record in self._records if record.phase is phase
                ),
            )
            for phase in PatchForgePhase
            if any(record.phase is phase for record in self._records)
        ]
        return RunBudgetUsage(
            phases=phases,
            total_tool_calls=len(self._records),
            total_duration_seconds=0,
            total_output_bytes=sum(record.output_bytes for record in self._records),
            finalization_reserve_used=any(
                record.phase is PatchForgePhase.FINALIZE for record in self._records
            ),
        )

    @staticmethod
    def requires_workspace(tool_name: ToolName) -> bool:
        return tool_name not in FakeGateway._WORKSPACE_FREE

    def refresh_workspace(self, workspace: WorkspaceHandle) -> None:
        self.events.append("refresh")
        self.refresh_calls += 1
        self.workspace = workspace

    def invoke(
        self,
        tool_name: ToolName,
        arguments: Mapping[str, object],
        *,
        phase: PatchForgePhase,
    ) -> GatewayResult:
        if self.failure_code is not None and tool_name is ToolName.GIT_STATUS:
            output = FailureOutput(code=self.failure_code, detail="scripted failure")
            status = ToolCallStatus.FAILED
            error_code = self.failure_code
        elif tool_name is ToolName.ADVANCE_PHASE:
            output = PhaseRequestOutput(
                current_phase=phase,
                target_phase=PatchForgePhase(arguments["target_phase"]),
            )
            status = ToolCallStatus.SUCCEEDED
            error_code = None
        elif tool_name is ToolName.SUBMIT_REPORT:
            report = AgentReport.model_validate(arguments["report"])
            output = ReportOutput(
                report_sha256=canonical_sha256(report),
                evidence_references=report.evidence_references,
            )
            status = ToolCallStatus.SUCCEEDED
            error_code = None
        else:
            output = StatusOutput(porcelain="", clean=True)
            status = ToolCallStatus.SUCCEEDED
            error_code = None
        record = ToolCallRecord(
            call_id=UUID(int=100 + len(self._records)),
            run_id=self.identity.run_id,
            sequence=len(self._records) + 1,
            phase=phase,
            tool_name=tool_name,
            arguments_sha256=canonical_sha256(dict(arguments)),
            status=status,
            started_at=NOW,
            completed_at=NOW,
            output_bytes=len(output.model_dump_json().encode("utf-8")),
            output_truncated=False,
            error_code=error_code,
        )
        self._records.append(record)
        return GatewayResult(record=record, output=output)


def _advance(target: PatchForgePhase) -> RuntimeToolAction:
    return RuntimeToolAction(
        tool_name=ToolName.ADVANCE_PHASE,
        arguments=AdvancePhaseArguments(target_phase=target),
    )


def _report() -> RuntimeToolAction:
    report = AgentReport(
        summary="Completed the bounded scripted run.",
        hypothesis="The scripted fixture had a deterministic defect.",
        implementation="Applied the scripted bounded correction.",
    )
    return RuntimeToolAction(
        tool_name=ToolName.SUBMIT_REPORT,
        arguments=SubmitReportArguments(report=report),
    )


def _happy_actions() -> list[RuntimeToolAction | Exception]:
    return [
        RuntimeToolAction(tool_name=ToolName.GIT_STATUS),
        _advance(PatchForgePhase.HYPOTHESIS),
        _advance(PatchForgePhase.REPRODUCE),
        _advance(PatchForgePhase.IMPLEMENT),
        _advance(PatchForgePhase.TARGETED_VALIDATE),
        _advance(PatchForgePhase.FULL_VALIDATE),
        _advance(PatchForgePhase.SELF_REVIEW),
        _advance(PatchForgePhase.FINALIZE),
        _report(),
    ]


def _runtime(
    *,
    actions: list[RuntimeToolAction | Exception],
    loops: int = 1,
    failure_code: str | None = None,
    cleanup_error: bool = False,
) -> tuple[PatchForgeRuntime, FakeGateway, FakeWorkspaceManager, list[str]]:
    events: list[str] = []
    manager = FakeWorkspaceManager(events, cleanup_error=cleanup_error)
    gateway = FakeGateway(
        events=events,
        workspace_manager=manager,
        budgets=_budgets(loops=loops),
        failure_code=failure_code,
    )
    runtime = PatchForgeRuntime(
        gateway=gateway,
        workspace_manager=manager,
        engine=ScriptedEngine(actions),
        lease_duration=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    return runtime, gateway, manager, events


def test_happy_path_uses_one_action_at_a_time_and_always_cleans_up() -> None:
    runtime, gateway, manager, events = _runtime(actions=_happy_actions())

    completion = runtime.execute()

    assert completion.snapshot.phase is PatchForgePhase.CLOSED
    assert completion.snapshot.outcome is None
    assert completion.snapshot.failure is None
    assert completion.report is not None
    assert completion.workspace_cleaned is True
    assert completion.budget_usage.total_tool_calls == len(completion.tool_calls)
    assert completion.budget_usage.finalization_reserve_used is True
    assert len(completion.lease_renewals) == 1
    assert gateway.refresh_calls == manager.renewals == 1
    assert events == ["renew", "refresh", "cleanup"]


def test_every_lease_renewal_is_immediately_followed_by_gateway_refresh() -> None:
    actions = [
        RuntimeToolAction(tool_name=ToolName.GIT_STATUS),
        RuntimeToolAction(
            tool_name=ToolName.GIT_DIFF,
            arguments=GitDiffArguments(),
        ),
        *_happy_actions()[1:],
    ]
    runtime, gateway, manager, events = _runtime(actions=actions)

    completion = runtime.execute()

    assert len(completion.lease_renewals) == 2
    assert gateway.refresh_calls == manager.renewals == 2
    assert events == ["renew", "refresh", "renew", "refresh", "cleanup"]


def test_real_gateway_workspace_requirement_is_closed_and_explicit() -> None:
    workspace_free = {
        ToolName.INSPECT_TEST_FAILURE,
        ToolName.ADVANCE_PHASE,
        ToolName.SUBMIT_REPORT,
    }

    assert {tool for tool in ToolName if not ToolGateway.requires_workspace(tool)} == workspace_free


def test_runtime_rejects_mismatched_manager_or_preused_gateway() -> None:
    events: list[str] = []
    bound_manager = FakeWorkspaceManager(events)
    other_manager = FakeWorkspaceManager(events)
    gateway = FakeGateway(
        events=events,
        workspace_manager=bound_manager,
    )
    engine = ScriptedEngine(_happy_actions())

    with pytest.raises(ValueError, match="bound workspace manager"):
        PatchForgeRuntime(
            gateway=gateway,
            workspace_manager=other_manager,
            engine=engine,
            lease_duration=timedelta(minutes=5),
        )

    gateway.invoke(ToolName.GIT_STATUS, {}, phase=PatchForgePhase.RECON)
    with pytest.raises(ValueError, match="fresh ToolGateway"):
        PatchForgeRuntime(
            gateway=gateway,
            workspace_manager=bound_manager,
            engine=engine,
            lease_duration=timedelta(minutes=5),
        )


def test_failed_refresh_after_renewal_is_a_workspace_failure() -> None:
    runtime, gateway, manager, events = _runtime(
        actions=[RuntimeToolAction(tool_name=ToolName.GIT_STATUS), _report()]
    )

    def reject_refresh(workspace: WorkspaceHandle) -> None:
        events.append("refresh")
        gateway.refresh_calls += 1
        raise GatewayRequestError("scripted refresh rejection")

    gateway.refresh_workspace = reject_refresh  # type: ignore[method-assign]
    completion = runtime.execute()

    assert manager.renewals == gateway.refresh_calls == 1
    assert events == ["renew", "refresh", "cleanup"]
    assert completion.snapshot.failure is PatchForgeFailure.WORKSPACE_ERROR
    assert completion.snapshot.outcome is PatchOutcome.ABORTED
    assert completion.lease_renewals == []


def test_invalid_phase_request_becomes_policy_failure_then_reports_and_cleans() -> None:
    runtime, _, manager, _ = _runtime(actions=[_advance(PatchForgePhase.FULL_VALIDATE), _report()])

    completion = runtime.execute()

    assert completion.snapshot.failure is PatchForgeFailure.POLICY_DENIED
    assert completion.snapshot.outcome is PatchOutcome.POLICY_VIOLATION
    assert completion.report is not None
    assert manager.cleanup_calls == 1


def test_loop_exhaustion_becomes_partial_budget_failure() -> None:
    actions = [
        _advance(PatchForgePhase.HYPOTHESIS),
        _advance(PatchForgePhase.REPRODUCE),
        _advance(PatchForgePhase.IMPLEMENT),
        _advance(PatchForgePhase.TARGETED_VALIDATE),
        _advance(PatchForgePhase.IMPLEMENT),
        _advance(PatchForgePhase.TARGETED_VALIDATE),
        _advance(PatchForgePhase.IMPLEMENT),
        _report(),
    ]
    runtime, _, _, _ = _runtime(actions=actions, loops=1)

    completion = runtime.execute()

    assert completion.snapshot.implementation_loops == 1
    assert completion.snapshot.failure is PatchForgeFailure.BUDGET_EXHAUSTED
    assert completion.snapshot.outcome is PatchOutcome.PARTIAL
    assert completion.report is not None


def test_gateway_budget_failure_preserves_final_report_reserve() -> None:
    runtime, gateway, _, _ = _runtime(
        actions=[RuntimeToolAction(tool_name=ToolName.GIT_STATUS), _report()],
        failure_code="output_budget_exceeded",
    )

    completion = runtime.execute()

    assert completion.snapshot.failure is PatchForgeFailure.BUDGET_EXHAUSTED
    assert completion.snapshot.outcome is PatchOutcome.PARTIAL
    assert completion.report is not None
    assert [item.tool_name for item in gateway.records] == [
        ToolName.GIT_STATUS,
        ToolName.SUBMIT_REPORT,
    ]


def test_engine_failure_can_finalize_with_a_partial_report() -> None:
    runtime, _, _, _ = _runtime(actions=[RuntimeError("engine failed"), _report()])

    completion = runtime.execute()

    assert completion.snapshot.failure is PatchForgeFailure.ENGINE_ERROR
    assert completion.snapshot.outcome is PatchOutcome.ABORTED
    assert completion.report is not None
    assert completion.workspace_cleaned is True


def test_unexpected_gateway_defect_propagates_after_cleanup() -> None:
    runtime, gateway, manager, events = _runtime(
        actions=[RuntimeToolAction(tool_name=ToolName.GIT_STATUS)]
    )

    def fail_programming_contract(
        tool_name: ToolName,
        arguments: Mapping[str, object],
        *,
        phase: PatchForgePhase,
    ) -> GatewayResult:
        raise AssertionError("scripted programming defect")

    gateway.invoke = fail_programming_contract  # type: ignore[method-assign]

    with pytest.raises(AssertionError, match="programming defect"):
        runtime.execute()

    assert manager.cleanup_calls == 1
    assert events == ["renew", "refresh", "cleanup"]
    assert runtime.lifecycle.snapshot.phase is PatchForgePhase.CLOSED


def test_repeated_engine_failure_in_finalization_still_cleans_up() -> None:
    runtime, _, manager, _ = _runtime(
        actions=[RuntimeError("engine failed"), RuntimeError("finalization failed")]
    )

    completion = runtime.execute()

    assert completion.snapshot.failure is PatchForgeFailure.ENGINE_ERROR
    assert completion.report is None
    assert completion.snapshot.phase is PatchForgePhase.CLOSED
    assert manager.cleanup_calls == 1


def test_cancellation_is_explicit_and_can_be_reported() -> None:
    runtime, _, _, _ = _runtime(actions=[RuntimeCancelledError("cancelled"), _report()])

    completion = runtime.execute()

    assert completion.snapshot.failure is PatchForgeFailure.CANCELLED
    assert completion.snapshot.outcome is PatchOutcome.CANCELLED
    assert completion.report is not None


def test_cleanup_failure_is_typed_and_closes_the_runtime() -> None:
    runtime, _, manager, _ = _runtime(actions=_happy_actions(), cleanup_error=True)

    completion = runtime.execute()

    assert completion.snapshot.phase is PatchForgePhase.CLOSED
    assert completion.snapshot.failure is PatchForgeFailure.CLEANUP_FAILED
    assert completion.snapshot.cleanup_failed is True
    assert completion.workspace_cleaned is False
    assert manager.cleanup_calls == 1


def test_runtime_tool_action_is_strict_and_has_no_parallel_shape() -> None:
    with pytest.raises(ValidationError):
        RuntimeToolAction.model_validate(
            {
                "tool_name": "git_status",
                "arguments": {},
                "parallel_actions": [],
            }
        )


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        shell=False,
    )
    return result.stdout.decode("utf-8").strip()


def _real_repository(path: Path) -> tuple[Path, str]:
    path.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(path)],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        shell=False,
    )
    _git(path, "config", "user.name", "Runtime Fixture")
    _git(path, "config", "user.email", "runtime@example.invalid")
    (path / "calculator.py").write_text(
        "def subtract(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    (path / "tests").mkdir()
    (path / "tests" / "test_calculator.py").write_text(
        "from calculator import subtract\n\ndef test_subtract():\n    assert subtract(5, 2) == 3\n",
        encoding="utf-8",
    )
    _git(path, "add", ".")
    _git(path, "commit", "-m", "fixture: broken subtraction")
    return path, _git(path, "rev-parse", "HEAD")


def _real_profile() -> RepositoryProfile:
    commands = {
        purpose: SandboxCommand(
            executable="python",
            arguments=["-m", "pytest", purpose.value],
            timeout_seconds=30,
            max_output_bytes=100_000,
        )
        for purpose in CommandPurpose
    }
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
        commands=commands,
        test_path_prefixes=["tests"],
    )


def test_real_gateway_workspace_and_fake_sandbox_complete_closed_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, source_sha = _real_repository(tmp_path / "source")
    profile = _real_profile()
    task = _task().model_copy(
        update={
            "source": SourceRevision(
                repository_url="https://example.invalid/repository",
                commit_sha=source_sha,
            ),
            "repository_profile_sha256": canonical_sha256(profile),
        }
    )
    identity = _identity(task).model_copy(
        update={
            "source": task.source,
            "repository_profile_sha256": canonical_sha256(profile),
            "task_sha256": canonical_sha256(task),
        }
    )
    budgets = _budgets()
    policy = PatchForgePolicy(
        repository_profile_id=profile.profile_id,
        repository_profile_sha256=canonical_sha256(profile),
        allowed_tools=list(ToolName),
        budgets=budgets,
        max_changed_files=20,
        max_diff_bytes=100_000,
        allow_test_file_changes=True,
    )
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    handle = manager.provision(
        task,
        profile,
        identity.run_id,
        source,
        lease_duration=timedelta(minutes=5),
    )
    execution_ids = iter([UUID(int=201), UUID(int=202), UUID(int=203)])
    sandbox = FakeSandbox(
        [
            FakeSandboxPlan(
                expected_command_sha256=canonical_sha256(
                    profile.commands[CommandPurpose.REPRODUCTION]
                ),
                status=SandboxStatus.FAILED,
                exit_code=1,
                stderr=b"assert 7 == 3\n",
                error_code="command_failed",
            ),
            FakeSandboxPlan(
                expected_command_sha256=canonical_sha256(
                    profile.commands[CommandPurpose.TARGETED_TESTS]
                ),
                status=SandboxStatus.SUCCEEDED,
                exit_code=0,
                stdout=b"1 passed\n",
            ),
            FakeSandboxPlan(
                expected_command_sha256=canonical_sha256(
                    profile.commands[CommandPurpose.FULL_TEST_SUITE]
                ),
                status=SandboxStatus.SUCCEEDED,
                exit_code=0,
                stdout=b"1 passed\n",
            ),
        ],
        clock=lambda: NOW,
        id_factory=lambda: next(execution_ids),
    )
    gateway = ToolGateway(
        identity=identity,
        task=task,
        profile=profile,
        policy=policy,
        workspace=handle,
        workspace_manager=manager,
        sandbox=sandbox,
        clock=lambda: NOW,
    )
    refresh_calls = 0
    original_refresh = gateway.refresh_workspace

    def track_refresh(workspace: WorkspaceHandle) -> None:
        nonlocal refresh_calls
        refresh_calls += 1
        original_refresh(workspace)

    monkeypatch.setattr(gateway, "refresh_workspace", track_refresh)
    original = (handle.worktree / "calculator.py").read_bytes()
    fixed = "def subtract(a, b):\n    return a - b\n"
    actions: list[RuntimeToolAction | Exception] = [
        RuntimeToolAction(
            tool_name=ToolName.LIST_TREE,
            arguments=ListTreeArguments(),
        ),
        _advance(PatchForgePhase.HYPOTHESIS),
        RuntimeToolAction(
            tool_name=ToolName.READ_FILE_RANGE,
            arguments=ReadFileRangeArguments(
                path="calculator.py",
                start_line=1,
                end_line=2,
            ),
        ),
        _advance(PatchForgePhase.REPRODUCE),
        RuntimeToolAction(tool_name=ToolName.RUN_TARGETED_TESTS),
        _advance(PatchForgePhase.IMPLEMENT),
        RuntimeToolAction(
            tool_name=ToolName.WRITE_PATCH,
            arguments=WritePatchArguments(
                path="calculator.py",
                expected_sha256=sha256(original).hexdigest(),
                content=fixed,
            ),
        ),
        _advance(PatchForgePhase.TARGETED_VALIDATE),
        RuntimeToolAction(tool_name=ToolName.RUN_TARGETED_TESTS),
        _advance(PatchForgePhase.FULL_VALIDATE),
        RuntimeToolAction(tool_name=ToolName.RUN_TEST_SUITE),
        _advance(PatchForgePhase.SELF_REVIEW),
        RuntimeToolAction(tool_name=ToolName.INSPECT_DIFF),
        _advance(PatchForgePhase.FINALIZE),
        RuntimeToolAction(
            tool_name=ToolName.GIT_DIFF,
            arguments=GitDiffArguments(max_output_bytes=100_000),
        ),
        _report(),
    ]
    runtime = PatchForgeRuntime(
        gateway=gateway,
        workspace_manager=manager,
        engine=ScriptedEngine(actions),
        lease_duration=timedelta(minutes=5),
        clock=lambda: NOW,
    )

    completion = runtime.execute()

    assert completion.snapshot.phase is PatchForgePhase.CLOSED
    assert completion.snapshot.failure is None
    assert completion.report is not None
    assert completion.workspace_cleaned is True
    assert len(completion.executions) == 3
    assert completion.budget_usage.total_tool_calls == len(completion.gateway_results)
    assert completion.budget_usage.finalization_reserve_used is True
    assert len(completion.lease_renewals) == refresh_calls == 8
    assert [request.command for request in sandbox.requests] == [
        profile.commands[CommandPurpose.REPRODUCTION],
        profile.commands[CommandPurpose.TARGETED_TESTS],
        profile.commands[CommandPurpose.FULL_TEST_SUITE],
    ]
    assert any(
        item.record.phase is PatchForgePhase.FINALIZE and item.record.tool_name is ToolName.GIT_DIFF
        for item in completion.gateway_results
    )
    assert completion.gateway_results[-1].record.tool_name is ToolName.SUBMIT_REPORT
    assert not handle.root.exists()
