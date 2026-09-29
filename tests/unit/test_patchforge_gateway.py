"""PatchForge typed tool gateway policy and evidence tests."""

import os
import subprocess
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from nexus.atlas.models import SourceRevision
from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.gateway import (
    DiffInspectionOutput,
    DiffOutput,
    ExecutionOutput,
    FailureOutput,
    FileOutput,
    GatewayBudgetError,
    GatewayRequestError,
    GatewayReserveRefusal,
    MutationOutput,
    PhaseRequestOutput,
    ReportOutput,
    SearchOutput,
    StatusOutput,
    SymbolOutput,
    ToolGateway,
    TreeOutput,
)
from nexus.patchforge.models import (
    AgentReport,
    EngineeringTask,
    PatchForgePhase,
    PhaseBudget,
    RunBudgets,
    RunIdentity,
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
from nexus.patchforge.sandbox import FakeSandbox, FakeSandboxPlan, SandboxStatus
from nexus.patchforge.workspace import WorkspaceError, WorkspaceHandle, WorkspaceManager

NOW = datetime(2026, 9, 28, 3, tzinfo=UTC)


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
    path.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(path)],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        shell=False,
    )
    _git(path, "config", "user.name", "PatchForge Fixture")
    _git(path, "config", "user.email", "fixture@example.invalid")
    (path / "calculator.py").write_text(
        "def add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    (path / "tests").mkdir()
    (path / "tests" / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    (path / "protected.txt").write_text("operator owned\n", encoding="utf-8")
    (path / ".env").write_text("SECRET=fixture-only\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "fixture: initial")
    return path, _git(path, "rev-parse", "HEAD")


def _phase_budget(*, calls: int = 20, output: int = 1_000_000) -> PhaseBudget:
    return PhaseBudget(
        max_tool_calls=calls,
        max_duration_seconds=120,
        max_output_bytes=output,
    )


def _budgets(*, recon_calls: int = 20, recon_output: int = 1_000_000) -> RunBudgets:
    return RunBudgets(
        provisioning=_phase_budget(),
        recon=_phase_budget(calls=recon_calls, output=recon_output),
        hypothesis=_phase_budget(),
        reproduce=_phase_budget(),
        implement=_phase_budget(),
        targeted_validate=_phase_budget(),
        full_validate=_phase_budget(),
        self_review=_phase_budget(),
        finalization_reserve=_phase_budget(calls=2),
        cleanup=_phase_budget(),
        max_implementation_loops=2,
        max_total_tool_calls=182,
        max_total_duration_seconds=1200,
    )


def _profile() -> RepositoryProfile:
    commands = {
        purpose: SandboxCommand(
            executable="python",
            arguments=["-m", "pytest", purpose.value],
            working_directory=".",
            timeout_seconds=30,
            max_output_bytes=100_000,
        )
        for purpose in CommandPurpose
    }
    return RepositoryProfile(
        profile_id="fixture.python",
        profile_version=1,
        repository_url="https://example.invalid/fixtures/calculator",
        sandbox=SandboxPolicy(
            image=f"sha256:{'a' * 64}",
            run_as_user="10001:10001",
            cpu_limit_millis=1000,
            memory_limit_mb=256,
            pids_limit=64,
            default_timeout_seconds=60,
            max_output_bytes=100_000,
        ),
        commands=commands,
        protected_paths=["protected.txt"],
        test_path_prefixes=["tests"],
    )


def _gateway(
    tmp_path: Path,
    *,
    allowed_tools: list[ToolName] | None = None,
    sandbox: FakeSandbox | None = None,
    budgets: RunBudgets | None = None,
    scope_paths: list[str] | None = None,
    allow_test_file_changes: bool = True,
) -> tuple[ToolGateway, WorkspaceHandle, RepositoryProfile]:
    source, source_sha = _repository(tmp_path / "source")
    profile = _profile()
    task = EngineeringTask(
        task_id=UUID(int=2),
        atlas_job_id=UUID(int=3),
        title="Fix subtraction",
        instructions="Correct subtraction and add focused coverage.",
        acceptance_criteria=["Subtraction returns the expected result."],
        source=SourceRevision(
            repository_url="https://example.invalid/fixtures/calculator",
            commit_sha=source_sha,
        ),
        repository_profile_id=profile.profile_id,
        repository_profile_sha256=canonical_sha256(profile),
        scope_paths=[] if scope_paths is None else scope_paths,
        created_at=NOW,
    )
    run_id = UUID(int=10)
    identity = RunIdentity(
        run_id=run_id,
        task_id=task.task_id,
        atlas_job_id=task.atlas_job_id,
        atlas_execution_id=UUID(int=4),
        agent_id="patchforge.engineer",
        source=task.source,
        repository_profile_id=profile.profile_id,
        repository_profile_sha256=canonical_sha256(profile),
        task_sha256=canonical_sha256(task),
        engine_kind="scripted",
        engine_version="v1",
        created_at=NOW,
    )
    selected_budgets = budgets or _budgets()
    policy = PatchForgePolicy(
        repository_profile_id=profile.profile_id,
        repository_profile_sha256=canonical_sha256(profile),
        allowed_tools=list(ToolName) if allowed_tools is None else allowed_tools,
        budgets=selected_budgets,
        max_changed_files=20,
        max_diff_bytes=1_000_000,
        allow_test_file_changes=allow_test_file_changes,
    )
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    handle = manager.provision(
        task,
        profile,
        run_id,
        source,
        lease_duration=timedelta(minutes=5),
    )
    gateway = ToolGateway(
        identity=identity,
        task=task,
        profile=profile,
        policy=policy,
        workspace=handle,
        workspace_manager=manager,
        sandbox=sandbox or FakeSandbox([]),
        clock=lambda: NOW,
    )
    return gateway, handle, profile


def test_read_tools_are_bounded_typed_and_hide_sensitive_files(tmp_path: Path) -> None:
    gateway, _, _ = _gateway(tmp_path)

    tree = gateway.invoke(ToolName.LIST_TREE, {}, phase=PatchForgePhase.RECON)
    assert isinstance(tree.output, TreeOutput)
    assert ".env" not in {entry.path for entry in tree.output.entries}
    assert tree.output.omitted_sensitive == 1

    search = gateway.invoke(
        ToolName.SEARCH_CODE,
        {"query": "return a + b"},
        phase=PatchForgePhase.RECON,
    )
    assert isinstance(search.output, SearchOutput)
    assert [(item.path, item.line) for item in search.output.matches] == [("calculator.py", 2)]

    read = gateway.invoke(
        ToolName.READ_FILE_RANGE,
        {"path": "calculator.py", "start_line": 1, "end_line": 2},
        phase=PatchForgePhase.RECON,
    )
    assert isinstance(read.output, FileOutput)
    assert read.output.content == "def add(a, b):\n    return a + b\n"

    symbol = gateway.invoke(
        ToolName.INSPECT_SYMBOL,
        {"path": "calculator.py", "symbol": "add"},
        phase=PatchForgePhase.RECON,
    )
    assert isinstance(symbol.output, SymbolOutput)
    assert symbol.output.symbol_kind == "function"
    assert [item.sequence for item in gateway.records] == [1, 2, 3, 4]


def test_sensitive_read_is_denied_with_runtime_evidence(tmp_path: Path) -> None:
    gateway, _, _ = _gateway(tmp_path)
    result = gateway.invoke(
        ToolName.READ_FILE_RANGE,
        {"path": ".env", "start_line": 1, "end_line": 1},
        phase=PatchForgePhase.RECON,
    )
    assert result.record.status is ToolCallStatus.DENIED
    assert isinstance(result.output, FailureOutput)
    assert result.output.code == "policy_denied"


def test_git_and_compare_and_swap_write_tools_produce_attested_diff(tmp_path: Path) -> None:
    gateway, handle, _ = _gateway(tmp_path)
    calculator = handle.worktree / "calculator.py"
    original_hash = sha256(calculator.read_bytes()).hexdigest()

    updated = gateway.invoke(
        ToolName.WRITE_PATCH,
        {
            "path": "calculator.py",
            "expected_sha256": original_hash,
            "content": (
                "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"
            ),
        },
        phase=PatchForgePhase.IMPLEMENT,
    )
    assert isinstance(updated.output, MutationOutput)
    assert updated.output.operation == "updated"

    created = gateway.invoke(
        ToolName.CREATE_FILE,
        {"path": "notes.txt", "content": "bounded note\n"},
        phase=PatchForgePhase.IMPLEMENT,
    )
    assert isinstance(created.output, MutationOutput)
    note_hash = cast_mutation(created.output).after_sha256
    assert note_hash is not None

    deleted = gateway.invoke(
        ToolName.DELETE_FILE,
        {"path": "notes.txt", "expected_sha256": note_hash},
        phase=PatchForgePhase.IMPLEMENT,
    )
    assert isinstance(deleted.output, MutationOutput)
    assert deleted.output.operation == "deleted"

    status = gateway.invoke(ToolName.GIT_STATUS, {}, phase=PatchForgePhase.IMPLEMENT)
    assert isinstance(status.output, StatusOutput)
    assert status.output.clean is False
    diff = gateway.invoke(
        ToolName.GIT_DIFF,
        {"max_output_bytes": 100_000},
        phase=PatchForgePhase.IMPLEMENT,
    )
    assert isinstance(diff.output, DiffOutput)
    assert diff.output.changed_files == ["calculator.py"]
    inspection = gateway.invoke(
        ToolName.INSPECT_DIFF,
        {},
        phase=PatchForgePhase.SELF_REVIEW,
    )
    assert isinstance(inspection.output, DiffInspectionOutput)
    assert inspection.output.scope_violations == []


def cast_mutation(output: MutationOutput) -> MutationOutput:
    return output


@pytest.mark.parametrize(
    ("path", "scope", "allow_tests", "message"),
    [
        ("protected.txt", None, True, "Protected"),
        ("calculator.py", ["tests"], True, "outside"),
        ("tests/new_test.py", None, False, "Test-file"),
        (".env", None, True, "Sensitive"),
    ],
)
def test_write_policy_denies_protected_scope_test_and_sensitive_paths(
    tmp_path: Path,
    path: str,
    scope: list[str] | None,
    allow_tests: bool,
    message: str,
) -> None:
    gateway, _, _ = _gateway(
        tmp_path,
        scope_paths=scope,
        allow_test_file_changes=allow_tests,
    )
    result = gateway.invoke(
        ToolName.CREATE_FILE,
        {"path": path, "content": "new\n"},
        phase=PatchForgePhase.IMPLEMENT,
    )
    assert result.record.status is ToolCallStatus.DENIED
    assert isinstance(result.output, FailureOutput)
    assert message in result.output.detail


def test_stale_write_and_symlink_access_are_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, handle, _ = _gateway(tmp_path)
    stale = gateway.invoke(
        ToolName.WRITE_PATCH,
        {"path": "calculator.py", "expected_sha256": "f" * 64, "content": "changed\n"},
        phase=PatchForgePhase.IMPLEMENT,
    )
    assert stale.record.status is ToolCallStatus.DENIED
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == handle.worktree / "calculator.py" or original_is_symlink(path),
    )
    linked = gateway.invoke(
        ToolName.READ_FILE_RANGE,
        {"path": "calculator.py", "start_line": 1, "end_line": 1},
        phase=PatchForgePhase.RECON,
    )
    assert linked.record.status is ToolCallStatus.DENIED


def test_capability_and_phase_policy_denials_are_recorded_without_side_effects(
    tmp_path: Path,
) -> None:
    gateway, handle, _ = _gateway(tmp_path, allowed_tools=[ToolName.LIST_TREE])
    denied_capability = gateway.invoke(
        ToolName.CREATE_FILE,
        {"path": "forbidden.txt", "content": "no\n"},
        phase=PatchForgePhase.IMPLEMENT,
    )
    denied_phase = gateway.invoke(
        ToolName.LIST_TREE,
        {},
        phase=PatchForgePhase.FINALIZE,
    )
    assert denied_capability.record.status is ToolCallStatus.DENIED
    assert denied_phase.record.status is ToolCallStatus.DENIED
    assert not (handle.worktree / "forbidden.txt").exists()


def test_execution_uses_fixed_profile_command_and_supports_failure_inspection(
    tmp_path: Path,
) -> None:
    profile = _profile()
    command = profile.commands[CommandPurpose.TARGETED_TESTS]
    sandbox = FakeSandbox(
        [
            FakeSandboxPlan(
                expected_command_sha256=canonical_sha256(command),
                status=SandboxStatus.FAILED,
                exit_code=1,
                stdout=b"one failed\n",
                stderr=b"assert 4 == 5\n",
                error_code="command_failed",
            )
        ],
        clock=lambda: NOW,
        id_factory=lambda: UUID(int=80),
    )
    gateway, _, _ = _gateway(tmp_path, sandbox=sandbox)
    execution = gateway.invoke(
        ToolName.RUN_TARGETED_TESTS,
        {},
        phase=PatchForgePhase.TARGETED_VALIDATE,
    )
    assert execution.record.status is ToolCallStatus.FAILED
    assert isinstance(execution.output, ExecutionOutput)
    assert execution.output.command_name == CommandPurpose.TARGETED_TESTS
    assert execution.sandbox_execution is not None
    assert sandbox.requests[0].command == command

    inspection = gateway.invoke(
        ToolName.INSPECT_TEST_FAILURE,
        {"tool_call_id": execution.record.call_id, "max_output_bytes": 12},
        phase=PatchForgePhase.TARGETED_VALIDATE,
    )
    assert isinstance(inspection.output, ExecutionOutput)
    assert inspection.output.output_truncated is True
    assert len(inspection.output.stdout.encode()) + len(inspection.output.stderr.encode()) <= 12


@pytest.mark.parametrize(
    ("tool_name", "phase", "purpose"),
    [
        (ToolName.RUN_TARGETED_TESTS, PatchForgePhase.REPRODUCE, CommandPurpose.REPRODUCTION),
        (
            ToolName.RUN_TARGETED_TESTS,
            PatchForgePhase.TARGETED_VALIDATE,
            CommandPurpose.TARGETED_TESTS,
        ),
        (ToolName.RUN_TEST_SUITE, PatchForgePhase.FULL_VALIDATE, CommandPurpose.FULL_TEST_SUITE),
        (ToolName.RUN_FORMATTER, PatchForgePhase.TARGETED_VALIDATE, CommandPurpose.FORMATTER),
        (ToolName.RUN_LINTER, PatchForgePhase.TARGETED_VALIDATE, CommandPurpose.LINTER),
        (ToolName.RUN_TYPECHECK, PatchForgePhase.FULL_VALIDATE, CommandPurpose.TYPECHECK),
    ],
)
def test_each_execution_tool_uses_the_phase_specific_operator_command(
    tmp_path: Path,
    tool_name: ToolName,
    phase: PatchForgePhase,
    purpose: CommandPurpose,
) -> None:
    profile = _profile()
    command = profile.commands[purpose]
    sandbox = FakeSandbox(
        [
            FakeSandboxPlan(
                expected_command_sha256=canonical_sha256(command),
                status=SandboxStatus.SUCCEEDED,
                exit_code=0,
            )
        ],
        clock=lambda: NOW,
    )
    gateway, _, _ = _gateway(tmp_path, sandbox=sandbox)
    result = gateway.invoke(tool_name, {}, phase=phase)
    assert result.record.status is ToolCallStatus.SUCCEEDED
    assert isinstance(result.output, ExecutionOutput)
    assert result.output.command_name == purpose.value
    assert sandbox.requests[0].command == command


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (SandboxStatus.SUCCEEDED, ToolCallStatus.SUCCEEDED),
        (SandboxStatus.TIMED_OUT, ToolCallStatus.TIMED_OUT),
        (SandboxStatus.SANDBOX_ERROR, ToolCallStatus.FAILED),
    ],
)
def test_sandbox_status_is_mapped_to_runtime_tool_evidence(
    tmp_path: Path,
    status: SandboxStatus,
    expected: ToolCallStatus,
) -> None:
    profile = _profile()
    command = profile.commands[CommandPurpose.FULL_TEST_SUITE]
    plan = FakeSandboxPlan(
        expected_command_sha256=canonical_sha256(command),
        status=status,
        exit_code=0 if status is SandboxStatus.SUCCEEDED else None,
        error_code=None if status is SandboxStatus.SUCCEEDED else "sandbox_error",
    )
    gateway, _, _ = _gateway(tmp_path, sandbox=FakeSandbox([plan], clock=lambda: NOW))
    result = gateway.invoke(
        ToolName.RUN_TEST_SUITE,
        {},
        phase=PatchForgePhase.FULL_VALIDATE,
    )
    assert result.record.status is expected


def test_budget_exhaustion_preserves_finalization_reserve(tmp_path: Path) -> None:
    gateway, _, _ = _gateway(tmp_path, budgets=_budgets(recon_calls=1))
    gateway.invoke(ToolName.LIST_TREE, {}, phase=PatchForgePhase.RECON)
    with pytest.raises(GatewayBudgetError, match="exhausted"):
        gateway.invoke(ToolName.LIST_TREE, {}, phase=PatchForgePhase.RECON)
    assert len(gateway.records) == 1


def test_tiny_output_budget_rejects_before_a_call_is_started(tmp_path: Path) -> None:
    gateway, _, _ = _gateway(tmp_path, budgets=_budgets(recon_output=511))
    with pytest.raises(GatewayBudgetError, match="output budget"):
        gateway.invoke(ToolName.LIST_TREE, {}, phase=PatchForgePhase.RECON)
    assert gateway.records == ()


def test_control_tools_emit_requests_without_mutating_lifecycle(tmp_path: Path) -> None:
    gateway, _, _ = _gateway(tmp_path)
    phase = gateway.invoke(
        ToolName.ADVANCE_PHASE,
        {"target_phase": PatchForgePhase.HYPOTHESIS},
        phase=PatchForgePhase.RECON,
    )
    assert isinstance(phase.output, PhaseRequestOutput)
    assert phase.output.current_phase is PatchForgePhase.RECON

    report = AgentReport(
        summary="Prepared a bounded patch.",
        hypothesis="The implementation used the wrong operator.",
        implementation="Corrected the operator and added coverage.",
    )
    submitted = gateway.invoke(
        ToolName.SUBMIT_REPORT,
        {"report": report.model_dump(mode="python")},
        phase=PatchForgePhase.FINALIZE,
    )
    assert isinstance(submitted.output, ReportOutput)
    assert submitted.output.report_sha256 == canonical_sha256(report)
    assert [item.phase for item in gateway.records] == [
        PatchForgePhase.RECON,
        PatchForgePhase.FINALIZE,
    ]


def test_invalid_arguments_are_rejected_before_evidence_is_created(tmp_path: Path) -> None:
    gateway, _, _ = _gateway(tmp_path)
    with pytest.raises(GatewayRequestError, match="strict validation"):
        gateway.invoke(
            ToolName.READ_FILE_RANGE,
            {"path": "calculator.py", "start_line": "1", "end_line": 2},
            phase=PatchForgePhase.RECON,
        )
    assert gateway.records == ()


@pytest.mark.parametrize(
    ("identity_update", "expected"),
    [
        ({"atlas_job_id": UUID(int=999)}, "Atlas job"),
        ({"agent_id": "other.agent"}, "agent identity"),
        ({"task_sha256": "f" * 64}, "task hash"),
        (
            {
                "source": SourceRevision(
                    repository_url="https://example.invalid/fixtures/calculator",
                    commit_sha="f" * 40,
                )
            },
            "source",
        ),
    ],
)
def test_gateway_constructor_rejects_invalid_run_bindings(
    tmp_path: Path,
    identity_update: dict[str, object],
    expected: str,
) -> None:
    gateway, _, _ = _gateway(tmp_path)
    with pytest.raises(GatewayRequestError, match=expected):
        ToolGateway(
            identity=gateway.identity.model_copy(update=identity_update),
            task=gateway.task,
            profile=gateway.profile,
            policy=gateway.policy,
            workspace=gateway.workspace,
            workspace_manager=gateway.workspace_manager,
            sandbox=FakeSandbox([]),
            clock=lambda: NOW,
        )


def test_gateway_constructor_rejects_source_profile_and_workspace_mismatches(
    tmp_path: Path,
) -> None:
    gateway, _, _ = _gateway(tmp_path)
    changed_task = gateway.task.model_copy(
        update={
            "source": SourceRevision(
                repository_url="https://example.invalid/fixtures/other",
                commit_sha=gateway.task.source.commit_sha,
            )
        }
    )
    changed_identity = gateway.identity.model_copy(
        update={
            "source": changed_task.source,
            "task_sha256": canonical_sha256(changed_task),
        }
    )
    with pytest.raises(GatewayRequestError, match="repository URL"):
        ToolGateway(
            identity=changed_identity,
            task=changed_task,
            profile=gateway.profile,
            policy=gateway.policy,
            workspace=gateway.workspace,
            workspace_manager=gateway.workspace_manager,
            sandbox=FakeSandbox([]),
        )

    tampered_workspace = gateway.workspace.model_copy(
        update={"record": gateway.workspace.record.model_copy(update={"source_sha": "f" * 40})}
    )
    with pytest.raises(GatewayRequestError, match="source"):
        ToolGateway(
            identity=gateway.identity,
            task=gateway.task,
            profile=gateway.profile,
            policy=gateway.policy,
            workspace=tampered_workspace,
            workspace_manager=gateway.workspace_manager,
            sandbox=FakeSandbox([]),
        )

    wrong_root = gateway.workspace.model_copy(update={"root": tmp_path / "elsewhere"})
    with pytest.raises(GatewayRequestError, match="runtime verification"):
        ToolGateway(
            identity=gateway.identity,
            task=gateway.task,
            profile=gateway.profile,
            policy=gateway.policy,
            workspace=wrong_root,
            workspace_manager=gateway.workspace_manager,
            sandbox=FakeSandbox([]),
        )


def test_expected_workspace_and_sandbox_errors_become_failed_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, _, _ = _gateway(tmp_path)
    monkeypatch.setattr(
        gateway.workspace_manager,
        "status_porcelain",
        lambda handle: (_ for _ in ()).throw(WorkspaceError("status unavailable")),
    )
    workspace_failure = gateway.invoke(
        ToolName.GIT_STATUS,
        {},
        phase=PatchForgePhase.RECON,
    )
    assert workspace_failure.record.status is ToolCallStatus.FAILED
    assert workspace_failure.record.error_code == "tool_error"
    assert isinstance(workspace_failure.output, FailureOutput)

    sandbox_failure = gateway.invoke(
        ToolName.RUN_TARGETED_TESTS,
        {},
        phase=PatchForgePhase.TARGETED_VALIDATE,
    )
    assert sandbox_failure.record.status is ToolCallStatus.FAILED
    assert sandbox_failure.record.error_code == "sandbox_error"
    assert isinstance(sandbox_failure.output, FailureOutput)
    assert [record.sequence for record in gateway.records] == [1, 2]


def test_programming_errors_are_not_swallowed_as_tool_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, _, _ = _gateway(tmp_path)
    monkeypatch.setattr(
        gateway,
        "_dispatch",
        lambda *args: (_ for _ in ()).throw(AssertionError("programming defect")),
    )
    with pytest.raises(AssertionError, match="programming defect"):
        gateway.invoke(ToolName.LIST_TREE, {}, phase=PatchForgePhase.RECON)
    assert gateway.records == ()


def test_all_bounded_repository_outputs_attest_truncation(tmp_path: Path) -> None:
    gateway, handle, _ = _gateway(tmp_path)
    tree = gateway.invoke(
        ToolName.LIST_TREE,
        {"max_entries": 1},
        phase=PatchForgePhase.RECON,
    )
    search = gateway.invoke(
        ToolName.SEARCH_CODE,
        {"query": "a", "max_results": 1},
        phase=PatchForgePhase.RECON,
    )
    file_range = gateway.invoke(
        ToolName.READ_FILE_RANGE,
        {
            "path": "calculator.py",
            "start_line": 1,
            "end_line": 2,
            "max_output_bytes": 1,
        },
        phase=PatchForgePhase.RECON,
    )
    (handle.worktree / "calculator.py").write_text("changed\n", encoding="utf-8")
    diff = gateway.invoke(
        ToolName.GIT_DIFF,
        {"max_output_bytes": 1},
        phase=PatchForgePhase.RECON,
    )
    assert [result.record.output_truncated for result in (tree, search, file_range, diff)] == [
        True,
        True,
        True,
        True,
    ]


def test_phase_output_exhaustion_suppresses_oversized_payload(tmp_path: Path) -> None:
    gateway, handle, _ = _gateway(tmp_path, budgets=_budgets(recon_output=512))
    (handle.worktree / "calculator.py").write_text("x" * 10_000, encoding="utf-8")
    result = gateway.invoke(
        ToolName.READ_FILE_RANGE,
        {
            "path": "calculator.py",
            "start_line": 1,
            "end_line": 1,
            "max_output_bytes": 100_000,
        },
        phase=PatchForgePhase.RECON,
    )
    assert result.record.status is ToolCallStatus.FAILED
    assert result.record.error_code == "output_budget_exceeded"
    assert result.record.output_truncated is True
    assert result.record.output_bytes <= 512
    assert isinstance(result.output, FailureOutput)


@pytest.mark.parametrize("shell", ["sh", "bash", "cmd", "powershell", "pwsh"])
def test_repository_profile_commands_cannot_invoke_a_shell(shell: str) -> None:
    with pytest.raises(ValidationError, match="cannot invoke a shell"):
        SandboxCommand(
            executable=shell,
            timeout_seconds=10,
            max_output_bytes=1000,
        )


# Milestone D hardening: one deterministic regression per adversarial-review finding.


def test_budget_overrun_after_execution_keeps_sandbox_evidence(tmp_path: Path) -> None:
    profile = _profile()
    command = profile.commands[CommandPurpose.TARGETED_TESTS]
    ticks = iter([NOW, NOW + timedelta(seconds=200)])
    sandbox = FakeSandbox(
        [
            FakeSandboxPlan(
                expected_command_sha256=canonical_sha256(command),
                status=SandboxStatus.SUCCEEDED,
                exit_code=0,
            )
        ],
        clock=lambda: NOW,
    )
    gateway, _, _ = _gateway(tmp_path, sandbox=sandbox)
    gateway._clock = lambda: next(ticks)
    result = gateway.invoke(
        ToolName.RUN_TARGETED_TESTS,
        {},
        phase=PatchForgePhase.TARGETED_VALIDATE,
    )
    assert result.record.status is ToolCallStatus.FAILED
    assert result.record.error_code == "duration_budget_exceeded"
    assert isinstance(result.output, FailureOutput)
    assert result.sandbox_execution is not None
    assert gateway.executions == (result.sandbox_execution,)


def test_finalize_reads_cannot_consume_the_report_reserve(tmp_path: Path) -> None:
    gateway, _, _ = _gateway(tmp_path)
    first = gateway.invoke(ToolName.GIT_STATUS, {}, phase=PatchForgePhase.FINALIZE)
    assert first.record.status is ToolCallStatus.SUCCEEDED
    with pytest.raises(GatewayBudgetError, match="reserved for report submission"):
        gateway.invoke(ToolName.GIT_DIFF, {}, phase=PatchForgePhase.FINALIZE)
    report = gateway.invoke(
        ToolName.SUBMIT_REPORT,
        {
            "report": AgentReport(
                summary="Finalized.",
                hypothesis="Fixture.",
                implementation="None.",
                evidence_references=[first.record.call_id],
            ).model_dump(mode="python")
        },
        phase=PatchForgePhase.FINALIZE,
    )
    assert report.record.status is ToolCallStatus.SUCCEEDED


@pytest.mark.parametrize("path", [".gitignore", "nested/.gitattributes"])
def test_git_control_files_cannot_be_written(tmp_path: Path, path: str) -> None:
    gateway, handle, _ = _gateway(tmp_path)
    (handle.worktree / "nested").mkdir()
    result = gateway.invoke(
        ToolName.CREATE_FILE,
        {"path": path, "content": "*\n"},
        phase=PatchForgePhase.IMPLEMENT,
    )
    assert result.record.status is ToolCallStatus.DENIED
    assert not (handle.worktree / path).exists()


def test_ignored_paths_cannot_be_written_and_ignore_tampering_fails_closed(
    tmp_path: Path,
) -> None:
    gateway, handle, _ = _gateway(tmp_path)
    (handle.worktree / ".gitignore").write_text("*.log\nhidden.py\n", encoding="utf-8")
    denied = gateway.invoke(
        ToolName.CREATE_FILE,
        {"path": "hidden.py", "content": "print('invisible')\n"},
        phase=PatchForgePhase.IMPLEMENT,
    )
    assert denied.record.status is ToolCallStatus.DENIED
    for tool in (ToolName.GIT_STATUS, ToolName.GIT_DIFF, ToolName.INSPECT_DIFF):
        result = gateway.invoke(tool, {}, phase=PatchForgePhase.SELF_REVIEW)
        assert result.record.status is ToolCallStatus.FAILED
        assert result.record.error_code == "tool_error"
        assert isinstance(result.output, FailureOutput)
        assert "ignore" in result.output.detail


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot create colon-containing filenames")
def test_unrepresentable_filenames_do_not_crash_repository_tools(tmp_path: Path) -> None:
    gateway, handle, _ = _gateway(tmp_path)
    (handle.worktree / "a:b.py").write_text("return a + b\n", encoding="utf-8")
    tree = gateway.invoke(ToolName.LIST_TREE, {}, phase=PatchForgePhase.RECON)
    assert isinstance(tree.output, TreeOutput)
    assert "a:b.py" not in {entry.path for entry in tree.output.entries}
    assert tree.output.omitted_unrepresentable == 1
    search = gateway.invoke(
        ToolName.SEARCH_CODE,
        {"query": "return a + b"},
        phase=PatchForgePhase.RECON,
    )
    assert isinstance(search.output, SearchOutput)
    assert [item.path for item in search.output.matches] == ["calculator.py"]
    assert search.output.skipped_files >= 1
    diff = gateway.invoke(ToolName.GIT_DIFF, {}, phase=PatchForgePhase.RECON)
    assert diff.record.status is ToolCallStatus.FAILED
    assert diff.record.error_code == "tool_error"


def test_every_workspace_tool_reverifies_handle_and_lease(tmp_path: Path) -> None:
    gateway, handle, _ = _gateway(tmp_path)
    manager = gateway.workspace_manager
    manager._clock = lambda: NOW + timedelta(minutes=10)
    for tool, arguments in (
        (ToolName.LIST_TREE, {}),
        (ToolName.READ_FILE_RANGE, {"path": "calculator.py", "start_line": 1, "end_line": 1}),
    ):
        expired = gateway.invoke(tool, arguments, phase=PatchForgePhase.RECON)
        assert expired.record.status is ToolCallStatus.FAILED
        assert isinstance(expired.output, FailureOutput)
        assert "lease" in expired.output.detail
    manager._clock = lambda: NOW
    renewed = manager.renew_lease(handle, timedelta(minutes=30))
    stale = gateway.invoke(ToolName.LIST_TREE, {}, phase=PatchForgePhase.RECON)
    assert stale.record.status is ToolCallStatus.FAILED
    gateway.refresh_workspace(renewed)
    fresh = gateway.invoke(ToolName.LIST_TREE, {}, phase=PatchForgePhase.RECON)
    assert fresh.record.status is ToolCallStatus.SUCCEEDED
    other = renewed.model_copy(update={"worktree": tmp_path})
    with pytest.raises(GatewayRequestError, match="workspace"):
        gateway.refresh_workspace(other)


def test_report_evidence_must_reference_runtime_records(tmp_path: Path) -> None:
    gateway, _, _ = _gateway(tmp_path)
    read = gateway.invoke(ToolName.LIST_TREE, {}, phase=PatchForgePhase.RECON)
    result = gateway.invoke(
        ToolName.SUBMIT_REPORT,
        {
            "report": AgentReport(
                summary="Finalized.",
                hypothesis="Fixture.",
                implementation="None.",
                evidence_references=[read.record.call_id, UUID(int=999)],
            ).model_dump(mode="python")
        },
        phase=PatchForgePhase.FINALIZE,
    )
    assert result.record.status is ToolCallStatus.DENIED
    assert isinstance(result.output, FailureOutput)
    assert "unknown" in result.output.detail


@pytest.mark.parametrize(
    "path",
    [
        "conftest.py",
        "pkg/conftest.py",
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "sitecustomize.py",
        "usercustomize.py",
        "coverage_hook.pth",
    ],
)
def test_test_infrastructure_changes_follow_test_file_policy(tmp_path: Path, path: str) -> None:
    gateway, handle, _ = _gateway(tmp_path, allow_test_file_changes=False)
    (handle.worktree / "pkg").mkdir()
    result = gateway.invoke(
        ToolName.CREATE_FILE,
        {"path": path, "content": "\n"},
        phase=PatchForgePhase.IMPLEMENT,
    )
    assert result.record.status is ToolCallStatus.DENIED
    assert not (handle.worktree / path).exists()
    (handle.worktree / path).write_text("\n", encoding="utf-8")
    inspection = gateway.invoke(ToolName.INSPECT_DIFF, {}, phase=PatchForgePhase.SELF_REVIEW)
    assert isinstance(inspection.output, DiffInspectionOutput)
    assert path in inspection.output.test_files_changed


def test_tool_cache_ignore_files_are_tolerated_but_other_nested_rules_fail_closed(
    tmp_path: Path,
) -> None:
    gateway, handle, _ = _gateway(tmp_path)
    for cache in (".pytest_cache", "pkg/.mypy_cache", ".ruff_cache"):
        (handle.worktree / cache).mkdir(parents=True)
        (handle.worktree / cache / ".gitignore").write_text("*\n", encoding="utf-8")
        (handle.worktree / cache / "state").write_text("cached\n", encoding="utf-8")
    status = gateway.invoke(ToolName.GIT_STATUS, {}, phase=PatchForgePhase.RECON)
    assert status.record.status is ToolCallStatus.SUCCEEDED
    diff = gateway.invoke(ToolName.GIT_DIFF, {}, phase=PatchForgePhase.RECON)
    assert diff.record.status is ToolCallStatus.SUCCEEDED

    (handle.worktree / "pkg" / ".gitignore").write_text("*\n", encoding="utf-8")
    hidden = gateway.invoke(ToolName.INSPECT_DIFF, {}, phase=PatchForgePhase.RECON)
    assert hidden.record.status is ToolCallStatus.FAILED
    (handle.worktree / "pkg" / ".gitignore").unlink()
    (handle.worktree / ".pytest_cache" / ".gitattributes").write_text("* -diff\n")
    attributes = gateway.invoke(ToolName.INSPECT_DIFF, {}, phase=PatchForgePhase.RECON)
    assert attributes.record.status is ToolCallStatus.FAILED


def test_only_report_reserve_refusals_use_the_reserve_error(tmp_path: Path) -> None:
    gateway, _, _ = _gateway(tmp_path, budgets=_budgets(recon_calls=1))
    gateway.invoke(ToolName.GIT_STATUS, {}, phase=PatchForgePhase.FINALIZE)
    with pytest.raises(GatewayReserveRefusal, match="reserved for report submission"):
        gateway.invoke(ToolName.GIT_STATUS, {}, phase=PatchForgePhase.FINALIZE)

    gateway.invoke(ToolName.LIST_TREE, {}, phase=PatchForgePhase.RECON)
    with pytest.raises(GatewayBudgetError, match="exhausted") as exhausted:
        gateway.invoke(ToolName.LIST_TREE, {}, phase=PatchForgePhase.RECON)
    assert not isinstance(exhausted.value, GatewayReserveRefusal)
    assert len(gateway.records) == 2
