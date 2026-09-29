"""Typed, policy-enforcing tool boundary for PatchForge v1."""

import ast
import os
import stat
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from pydantic import Field, StringConstraints, TypeAdapter, ValidationError, model_validator

from nexus.atlas.models import Identifier, Sha256, StrictModel
from nexus.patchforge.canonical import canonical_json, canonical_sha256
from nexus.patchforge.models import (
    AgentReport,
    EngineeringTask,
    PatchForgePhase,
    PhaseUsage,
    RepositoryPath,
    RunBudgetUsage,
    RunIdentity,
    ToolCallRecord,
    ToolCallStatus,
    ToolName,
)
from nexus.patchforge.policy import CommandPurpose, PatchForgePolicy, RepositoryProfile
from nexus.patchforge.sandbox import (
    SandboxError,
    SandboxExecution,
    SandboxExecutor,
    SandboxRequest,
    SandboxStatus,
)
from nexus.patchforge.workspace import (
    GIT_CONTROL_FILES,
    WorkspaceError,
    WorkspaceHandle,
    WorkspaceManager,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], UUID]
type RootPath = Literal["."] | RepositoryPath
Query = Annotated[str, StringConstraints(min_length=1, max_length=500)]
FileContent = Annotated[str, StringConstraints(max_length=1_000_000)]
MIN_RESULT_ENVELOPE_BYTES = 512
BUDGET_FAILURE_CODES = frozenset({"output_budget_exceeded", "duration_budget_exceeded"})
# Files that change how tests are collected or how Python starts, wherever they live.
TEST_INFRASTRUCTURE_NAMES = frozenset(
    {
        "conftest.py",
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "sitecustomize.py",
        "usercustomize.py",
    }
)
TEST_INFRASTRUCTURE_SUFFIXES = frozenset({".pth"})
_REPOSITORY_PATH: TypeAdapter[RepositoryPath] = TypeAdapter(RepositoryPath)


class GatewayError(RuntimeError):
    """The tool gateway cannot safely accept an invocation."""


class GatewayRequestError(GatewayError):
    """Tool arguments or runtime context are invalid."""


class GatewayBudgetError(GatewayError):
    """No visible budget remains for another tool invocation."""


class GatewayReserveRefusal(GatewayBudgetError):
    """A non-report tool would consume the capacity reserved for the final report."""


class EmptyArguments(StrictModel):
    pass


class ListTreeArguments(StrictModel):
    path: RootPath = "."
    max_depth: int = Field(default=4, ge=0, le=20)
    max_entries: int = Field(default=500, ge=1, le=5000)


class SearchCodeArguments(StrictModel):
    query: Query
    path: RootPath = "."
    case_sensitive: bool = True
    max_results: int = Field(default=100, ge=1, le=1000)


class ReadFileRangeArguments(StrictModel):
    path: RepositoryPath
    start_line: int = Field(default=1, ge=1, le=10_000_000)
    end_line: int = Field(ge=1, le=10_000_000)
    max_output_bytes: int = Field(default=100_000, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_range(self) -> "ReadFileRangeArguments":
        if self.end_line < self.start_line:
            raise ValueError("File range end must not precede start")
        return self


class InspectSymbolArguments(StrictModel):
    path: RepositoryPath
    symbol: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


class GitDiffArguments(StrictModel):
    max_output_bytes: int = Field(default=500_000, ge=1, le=10_000_000)


class WritePatchArguments(StrictModel):
    path: RepositoryPath
    expected_sha256: Sha256
    content: FileContent


class CreateFileArguments(StrictModel):
    path: RepositoryPath
    content: FileContent


class DeleteFileArguments(StrictModel):
    path: RepositoryPath
    expected_sha256: Sha256


class InspectTestFailureArguments(StrictModel):
    tool_call_id: UUID
    max_output_bytes: int = Field(default=100_000, ge=1, le=1_000_000)


class AdvancePhaseArguments(StrictModel):
    target_phase: PatchForgePhase


class SubmitReportArguments(StrictModel):
    report: AgentReport


ToolArguments = (
    EmptyArguments
    | ListTreeArguments
    | SearchCodeArguments
    | ReadFileRangeArguments
    | InspectSymbolArguments
    | GitDiffArguments
    | WritePatchArguments
    | CreateFileArguments
    | DeleteFileArguments
    | InspectTestFailureArguments
    | AdvancePhaseArguments
    | SubmitReportArguments
)


def parse_tool_arguments(
    tool_name: ToolName,
    arguments: Mapping[str, object],
) -> StrictModel:
    """Parse one tool's arguments against its exact strict contract."""

    argument_type = _ARGUMENT_TYPES[tool_name]
    try:
        return argument_type.model_validate(dict(arguments))
    except Exception as exc:
        raise GatewayRequestError("Tool arguments failed strict validation") from exc


class TreeEntry(StrictModel):
    path: RepositoryPath
    kind: Literal["file", "directory"]
    size_bytes: int | None = Field(default=None, ge=0)


class TreeOutput(StrictModel):
    kind: Literal["tree"] = "tree"
    entries: list[TreeEntry]
    truncated: bool
    omitted_unrepresentable: int = Field(default=0, ge=0)
    omitted_sensitive: int = Field(ge=0)
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"


class SearchMatch(StrictModel):
    path: RepositoryPath
    line: int = Field(ge=1)
    text: Annotated[str, StringConstraints(max_length=2000)]


class SearchOutput(StrictModel):
    kind: Literal["search"] = "search"
    matches: list[SearchMatch]
    truncated: bool
    skipped_files: int = Field(ge=0)
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"


class FileOutput(StrictModel):
    kind: Literal["file"] = "file"
    path: RepositoryPath
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=0)
    content: str
    content_sha256: Sha256
    truncated: bool
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"


class SymbolOutput(StrictModel):
    kind: Literal["symbol"] = "symbol"
    path: RepositoryPath
    symbol: str
    symbol_kind: Literal["function", "async_function", "class"]
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    source: str
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"


class StatusOutput(StrictModel):
    kind: Literal["git_status"] = "git_status"
    porcelain: str
    clean: bool
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"


class DiffOutput(StrictModel):
    kind: Literal["git_diff"] = "git_diff"
    base_sha: str
    diff_sha256: Sha256
    patch: str
    changed_files: list[RepositoryPath]
    binary_files: list[RepositoryPath]
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    truncated: bool
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"


class DiffInspectionOutput(StrictModel):
    kind: Literal["diff_inspection"] = "diff_inspection"
    base_sha: str
    diff_sha256: Sha256
    changed_files: list[RepositoryPath]
    binary_files: list[RepositoryPath]
    protected_path_violations: list[RepositoryPath]
    scope_violations: list[RepositoryPath]
    test_files_changed: list[RepositoryPath]
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"


class MutationOutput(StrictModel):
    kind: Literal["mutation"] = "mutation"
    path: RepositoryPath
    operation: Literal["updated", "created", "deleted"]
    before_sha256: Sha256 | None = None
    after_sha256: Sha256 | None = None
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"


class ExecutionOutput(StrictModel):
    kind: Literal["execution"] = "execution"
    execution_id: UUID
    command_name: Identifier
    status: SandboxStatus
    exit_code: int | None = Field(default=None, ge=0, le=255)
    stdout: str
    stderr: str
    output_truncated: bool
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"


class FailureOutput(StrictModel):
    kind: Literal["failure"] = "failure"
    code: Identifier
    detail: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"


class PhaseRequestOutput(StrictModel):
    kind: Literal["phase_request"] = "phase_request"
    current_phase: PatchForgePhase
    target_phase: PatchForgePhase
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"


class ReportOutput(StrictModel):
    kind: Literal["report"] = "report"
    report_sha256: Sha256
    evidence_references: list[UUID]
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"


ToolOutput = Annotated[
    TreeOutput
    | SearchOutput
    | FileOutput
    | SymbolOutput
    | StatusOutput
    | DiffOutput
    | DiffInspectionOutput
    | MutationOutput
    | ExecutionOutput
    | FailureOutput
    | PhaseRequestOutput
    | ReportOutput,
    Field(discriminator="kind"),
]


class GatewayResult(StrictModel):
    record: ToolCallRecord
    output: ToolOutput
    sandbox_execution: SandboxExecution | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "GatewayResult":
        execution = self.sandbox_execution
        if execution is not None:
            if execution.call_id != self.record.call_id or execution.run_id != self.record.run_id:
                raise ValueError("Sandbox evidence does not match the tool call")
            budget_failure = (
                isinstance(self.output, FailureOutput) and self.output.code in BUDGET_FAILURE_CODES
            )
            if not isinstance(self.output, ExecutionOutput) and not budget_failure:
                raise ValueError("Sandbox evidence requires an execution or budget-failure output")
        return self


_ARGUMENT_TYPES: dict[ToolName, type[StrictModel]] = {
    ToolName.LIST_TREE: ListTreeArguments,
    ToolName.SEARCH_CODE: SearchCodeArguments,
    ToolName.READ_FILE_RANGE: ReadFileRangeArguments,
    ToolName.INSPECT_SYMBOL: InspectSymbolArguments,
    ToolName.GIT_STATUS: EmptyArguments,
    ToolName.GIT_DIFF: GitDiffArguments,
    ToolName.INSPECT_DIFF: EmptyArguments,
    ToolName.WRITE_PATCH: WritePatchArguments,
    ToolName.CREATE_FILE: CreateFileArguments,
    ToolName.DELETE_FILE: DeleteFileArguments,
    ToolName.RUN_TARGETED_TESTS: EmptyArguments,
    ToolName.RUN_TEST_SUITE: EmptyArguments,
    ToolName.RUN_FORMATTER: EmptyArguments,
    ToolName.RUN_LINTER: EmptyArguments,
    ToolName.RUN_TYPECHECK: EmptyArguments,
    ToolName.INSPECT_TEST_FAILURE: InspectTestFailureArguments,
    ToolName.ADVANCE_PHASE: AdvancePhaseArguments,
    ToolName.SUBMIT_REPORT: SubmitReportArguments,
}

_READ_PHASES = {
    PatchForgePhase.RECON,
    PatchForgePhase.HYPOTHESIS,
    PatchForgePhase.REPRODUCE,
    PatchForgePhase.IMPLEMENT,
    PatchForgePhase.TARGETED_VALIDATE,
    PatchForgePhase.FULL_VALIDATE,
    PatchForgePhase.SELF_REVIEW,
}
_DIFF_PHASES = _READ_PHASES | {PatchForgePhase.FINALIZE}
_PHASES_BY_TOOL: dict[ToolName, set[PatchForgePhase]] = {
    ToolName.LIST_TREE: _READ_PHASES,
    ToolName.SEARCH_CODE: _READ_PHASES,
    ToolName.READ_FILE_RANGE: _READ_PHASES,
    ToolName.INSPECT_SYMBOL: _READ_PHASES,
    ToolName.GIT_STATUS: _DIFF_PHASES,
    ToolName.GIT_DIFF: _DIFF_PHASES,
    ToolName.INSPECT_DIFF: _DIFF_PHASES,
    ToolName.WRITE_PATCH: {PatchForgePhase.IMPLEMENT},
    ToolName.CREATE_FILE: {PatchForgePhase.IMPLEMENT},
    ToolName.DELETE_FILE: {PatchForgePhase.IMPLEMENT},
    ToolName.RUN_TARGETED_TESTS: {
        PatchForgePhase.REPRODUCE,
        PatchForgePhase.TARGETED_VALIDATE,
    },
    ToolName.RUN_TEST_SUITE: {PatchForgePhase.FULL_VALIDATE},
    ToolName.RUN_FORMATTER: {
        PatchForgePhase.TARGETED_VALIDATE,
        PatchForgePhase.FULL_VALIDATE,
    },
    ToolName.RUN_LINTER: {
        PatchForgePhase.TARGETED_VALIDATE,
        PatchForgePhase.FULL_VALIDATE,
    },
    ToolName.RUN_TYPECHECK: {
        PatchForgePhase.TARGETED_VALIDATE,
        PatchForgePhase.FULL_VALIDATE,
    },
    ToolName.INSPECT_TEST_FAILURE: {
        PatchForgePhase.REPRODUCE,
        PatchForgePhase.TARGETED_VALIDATE,
        PatchForgePhase.FULL_VALIDATE,
    },
    ToolName.ADVANCE_PHASE: _READ_PHASES,
    ToolName.SUBMIT_REPORT: {PatchForgePhase.FINALIZE},
}

_WORKSPACE_FREE_TOOLS = frozenset(
    {ToolName.INSPECT_TEST_FAILURE, ToolName.ADVANCE_PHASE, ToolName.SUBMIT_REPORT}
)

_COMMAND_BY_TOOL = {
    ToolName.RUN_TARGETED_TESTS: CommandPurpose.TARGETED_TESTS,
    ToolName.RUN_TEST_SUITE: CommandPurpose.FULL_TEST_SUITE,
    ToolName.RUN_FORMATTER: CommandPurpose.FORMATTER,
    ToolName.RUN_LINTER: CommandPurpose.LINTER,
    ToolName.RUN_TYPECHECK: CommandPurpose.TYPECHECK,
}
EXECUTION_TOOLS = frozenset(_COMMAND_BY_TOOL)


def command_purpose_for(tool_name: ToolName, phase: PatchForgePhase) -> CommandPurpose:
    """Return the operator-profile command an execution tool runs in a phase."""

    if tool_name not in _COMMAND_BY_TOOL:
        raise ValueError(f"Tool does not execute a repository command: {tool_name}")
    if tool_name is ToolName.RUN_TARGETED_TESTS and phase is PatchForgePhase.REPRODUCE:
        return CommandPurpose.REPRODUCTION
    return _COMMAND_BY_TOOL[tool_name]


def path_matches(path: str, prefixes: Sequence[str]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


def path_in_scope(path: str, scope_paths: Sequence[str]) -> bool:
    return not scope_paths or path_matches(path, scope_paths)


def is_test_path(path: str, profile: RepositoryProfile) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        path_matches(path, profile.test_path_prefixes)
        or name in TEST_INFRASTRUCTURE_NAMES
        or Path(name).suffix in TEST_INFRASTRUCTURE_SUFFIXES
    )


def is_sensitive_path(path: str) -> bool:
    parts = path.casefold().split("/")
    name = parts[-1]
    if ".git" in parts:
        return True
    if name.startswith(".env") and name != ".env.example":
        return True
    if name in {"credentials", "credentials.json", "id_rsa", "id_ed25519"}:
        return True
    return Path(name).suffix in {".key", ".pem", ".p12", ".pfx"}


class ToolGateway:
    """Expose only typed, bounded operations over one verified disposable workspace."""

    def __init__(
        self,
        *,
        identity: RunIdentity,
        task: EngineeringTask,
        profile: RepositoryProfile,
        policy: PatchForgePolicy,
        workspace: WorkspaceHandle,
        workspace_manager: WorkspaceManager,
        sandbox: SandboxExecutor,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        if identity.run_id != workspace.record.run_id:
            raise GatewayRequestError("Run identity does not match the workspace")
        if identity.task_id != task.task_id or task.task_id != workspace.record.task_id:
            raise GatewayRequestError("Task identity does not match the workspace")
        if identity.atlas_job_id != task.atlas_job_id:
            raise GatewayRequestError("Atlas job identity does not match the task")
        if identity.agent_id != policy.agent_id:
            raise GatewayRequestError("Run agent identity does not match gateway policy")
        if identity.source != task.source or workspace.record.source_sha != task.source.commit_sha:
            raise GatewayRequestError("Run source does not match the task and workspace")
        profile_hash = canonical_sha256(profile)
        if (
            task.repository_profile_id != profile.profile_id
            or identity.repository_profile_id != profile.profile_id
            or policy.repository_profile_id != profile.profile_id
            or task.repository_profile_sha256 != profile_hash
            or identity.repository_profile_sha256 != profile_hash
            or policy.repository_profile_sha256 != profile_hash
        ):
            raise GatewayRequestError("Repository profile binding does not match")
        if str(task.source.repository_url) != str(profile.repository_url):
            raise GatewayRequestError("Task repository URL does not match its profile")
        if identity.task_sha256 != canonical_sha256(task):
            raise GatewayRequestError("Run identity task hash does not match")
        try:
            workspace = workspace_manager.verify_active(workspace)
        except WorkspaceError as exc:
            raise GatewayRequestError("Workspace handle failed runtime verification") from exc
        self.identity = identity
        self.task = task
        self.profile = profile
        self.policy = policy
        self.workspace = workspace
        self.workspace_manager = workspace_manager
        self.sandbox = sandbox
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4
        self._records: list[ToolCallRecord] = []
        self._executions: dict[UUID, SandboxExecution] = {}
        self._phase_duration: dict[PatchForgePhase, float] = {}
        self._phase_output: dict[PatchForgePhase, int] = {}

    @property
    def records(self) -> tuple[ToolCallRecord, ...]:
        return tuple(self._records)

    @property
    def executions(self) -> tuple[SandboxExecution, ...]:
        return tuple(self._executions.values())

    @property
    def budget_usage(self) -> RunBudgetUsage:
        phases: list[PhaseUsage] = []
        for phase in PatchForgePhase:
            calls = sum(record.phase is phase for record in self._records)
            duration = self._phase_duration.get(phase, 0.0)
            output = self._phase_output.get(phase, 0)
            if calls or duration or output:
                phases.append(
                    PhaseUsage(
                        phase=phase,
                        tool_calls=calls,
                        duration_seconds=duration,
                        output_bytes=output,
                    )
                )
        return RunBudgetUsage(
            phases=phases,
            total_tool_calls=len(self._records),
            total_duration_seconds=sum(self._phase_duration.values()),
            total_output_bytes=sum(record.output_bytes for record in self._records),
            finalization_reserve_used=any(
                record.phase is PatchForgePhase.FINALIZE for record in self._records
            ),
        )

    @staticmethod
    def requires_workspace(tool_name: ToolName) -> bool:
        """Return whether an invocation must hold a verified active workspace lease."""

        return tool_name not in _WORKSPACE_FREE_TOOLS

    def invoke(
        self,
        tool_name: ToolName,
        arguments: Mapping[str, object],
        *,
        phase: PatchForgePhase,
    ) -> GatewayResult:
        parsed = parse_tool_arguments(tool_name, arguments)
        self._check_budget(tool_name, phase)
        call_id = self._id_factory()
        started_at = self._now()
        argument_hash = canonical_sha256(parsed)
        execution: SandboxExecution | None = None
        try:
            self._authorize(tool_name, phase)
            if tool_name not in _WORKSPACE_FREE_TOOLS:
                self.workspace = self.workspace_manager.verify_active(self.workspace)
            output, execution = self._dispatch(tool_name, parsed, phase, call_id)
            status, error_code = self._status_for_execution(execution)
        except GatewayError as exc:
            output = FailureOutput(code="policy_denied", detail=str(exc))
            status = ToolCallStatus.DENIED
            error_code = "policy_denied"
        except SandboxError as exc:
            output = FailureOutput(
                code="sandbox_error",
                detail=_failure_detail(exc, "Sandbox execution failed"),
            )
            status = ToolCallStatus.FAILED
            error_code = "sandbox_error"
        except (WorkspaceError, OSError, UnicodeError) as exc:
            output = FailureOutput(
                code="tool_error",
                detail=_failure_detail(exc, "Tool execution failed"),
            )
            status = ToolCallStatus.FAILED
            error_code = "tool_error"
        completed_at = self._now()
        elapsed = max(0.0, (completed_at - started_at).total_seconds())
        output_bytes = len(canonical_json(output).encode("utf-8"))
        output_truncated = _output_is_truncated(output)
        phase_budget = self.policy.budgets.for_phase(phase)
        output_limit = phase_budget.max_output_bytes - self._report_output_reserve(tool_name, phase)
        if self._phase_output.get(phase, 0) + output_bytes > output_limit:
            output = FailureOutput(
                code="output_budget_exceeded",
                detail="Tool output exceeded the remaining phase output budget.",
            )
            output_bytes = len(canonical_json(output).encode("utf-8"))
            output_truncated = True
            status = ToolCallStatus.FAILED
            error_code = "output_budget_exceeded"
        if self._phase_duration.get(phase, 0.0) + elapsed > phase_budget.max_duration_seconds:
            output = FailureOutput(
                code="duration_budget_exceeded",
                detail="Tool execution exceeded the remaining phase duration budget.",
            )
            output_bytes = len(canonical_json(output).encode("utf-8"))
            status = ToolCallStatus.FAILED
            error_code = "duration_budget_exceeded"
        record = ToolCallRecord(
            call_id=call_id,
            run_id=self.identity.run_id,
            sequence=len(self._records) + 1,
            phase=phase,
            tool_name=tool_name,
            arguments_sha256=argument_hash,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            output_bytes=output_bytes,
            output_truncated=output_truncated,
            error_code=error_code,
        )
        self._records.append(record)
        self._phase_duration[phase] = self._phase_duration.get(phase, 0.0) + elapsed
        self._phase_output[phase] = self._phase_output.get(phase, 0) + output_bytes
        if execution is not None:
            self._executions[call_id] = execution
        return GatewayResult(record=record, output=output, sandbox_execution=execution)

    def _authorize(self, tool_name: ToolName, phase: PatchForgePhase) -> None:
        if tool_name not in self.policy.allowed_tools:
            raise GatewayError(f"Tool is not allowed by policy: {tool_name}")
        if phase not in _PHASES_BY_TOOL[tool_name]:
            raise GatewayError(f"Tool {tool_name} is not allowed during {phase}")

    def refresh_workspace(self, workspace: WorkspaceHandle) -> None:
        """Adopt a renewed handle for the same verified workspace, such as after lease renewal."""
        current = self.workspace
        if (
            workspace.record.workspace_id != current.record.workspace_id
            or workspace.record.run_id != current.record.run_id
            or workspace.record.task_id != current.record.task_id
            or workspace.record.source_sha != current.record.source_sha
            or workspace.root != current.root
            or workspace.worktree != current.worktree
            or workspace.git_directory != current.git_directory
        ):
            raise GatewayRequestError("Refreshed handle does not identify the bound workspace")
        try:
            self.workspace = self.workspace_manager.verify_active(workspace)
        except WorkspaceError as exc:
            raise GatewayRequestError("Refreshed workspace failed runtime verification") from exc

    @staticmethod
    def _report_output_reserve(tool_name: ToolName, phase: PatchForgePhase) -> int:
        if phase is PatchForgePhase.FINALIZE and tool_name is not ToolName.SUBMIT_REPORT:
            return MIN_RESULT_ENVELOPE_BYTES
        return 0

    def _check_budget(self, tool_name: ToolName, phase: PatchForgePhase) -> None:
        try:
            budget = self.policy.budgets.for_phase(phase)
        except ValueError as exc:
            raise GatewayRequestError("Phase has no model tool budget") from exc
        phase_calls = sum(record.phase is phase for record in self._records)
        if phase_calls >= budget.max_tool_calls:
            raise GatewayBudgetError(f"Tool-call budget exhausted for {phase}")
        report_reserve = self._report_output_reserve(tool_name, phase)
        if report_reserve and phase_calls + 1 >= budget.max_tool_calls:
            raise GatewayReserveRefusal("Final tool call is reserved for report submission")
        remaining_output = budget.max_output_bytes - self._phase_output.get(phase, 0)
        if remaining_output - report_reserve < MIN_RESULT_ENVELOPE_BYTES:
            if report_reserve:
                raise GatewayReserveRefusal(
                    "Final output capacity is reserved for report submission"
                )
            raise GatewayBudgetError(f"Tool output budget exhausted for {phase}")
        total_limit = self.policy.budgets.max_total_tool_calls
        if phase is not PatchForgePhase.FINALIZE:
            total_limit -= self.policy.budgets.finalization_reserve.max_tool_calls
        if len(self._records) >= total_limit:
            raise GatewayBudgetError("Run tool-call budget is reserved for finalization")
        duration_limit = self.policy.budgets.max_total_duration_seconds
        if phase is not PatchForgePhase.FINALIZE:
            duration_limit -= self.policy.budgets.finalization_reserve.max_duration_seconds
        if sum(self._phase_duration.values()) >= duration_limit:
            raise GatewayBudgetError("Run duration budget is reserved for finalization")

    def _dispatch(
        self,
        tool_name: ToolName,
        arguments: StrictModel,
        phase: PatchForgePhase,
        call_id: UUID,
    ) -> tuple[ToolOutput, SandboxExecution | None]:
        if tool_name is ToolName.LIST_TREE:
            return self._list_tree(cast(ListTreeArguments, arguments)), None
        if tool_name is ToolName.SEARCH_CODE:
            return self._search_code(cast(SearchCodeArguments, arguments)), None
        if tool_name is ToolName.READ_FILE_RANGE:
            return self._read_file_range(cast(ReadFileRangeArguments, arguments)), None
        if tool_name is ToolName.INSPECT_SYMBOL:
            return self._inspect_symbol(cast(InspectSymbolArguments, arguments)), None
        if tool_name is ToolName.GIT_STATUS:
            return self._git_status(), None
        if tool_name is ToolName.GIT_DIFF:
            return self._git_diff(cast(GitDiffArguments, arguments)), None
        if tool_name is ToolName.INSPECT_DIFF:
            return self._inspect_diff(), None
        if tool_name is ToolName.WRITE_PATCH:
            return self._write_patch(cast(WritePatchArguments, arguments)), None
        if tool_name is ToolName.CREATE_FILE:
            return self._create_file(cast(CreateFileArguments, arguments)), None
        if tool_name is ToolName.DELETE_FILE:
            return self._delete_file(cast(DeleteFileArguments, arguments)), None
        if tool_name in _COMMAND_BY_TOOL:
            purpose = self._command_purpose(tool_name, phase)
            execution = self._execute_command(purpose, phase, call_id)
            return self._execution_output(purpose, execution), execution
        if tool_name is ToolName.INSPECT_TEST_FAILURE:
            return self._inspect_test_failure(cast(InspectTestFailureArguments, arguments)), None
        if tool_name is ToolName.ADVANCE_PHASE:
            phase_request = cast(AdvancePhaseArguments, arguments)
            return (
                PhaseRequestOutput(
                    current_phase=phase,
                    target_phase=phase_request.target_phase,
                ),
                None,
            )
        if tool_name is ToolName.SUBMIT_REPORT:
            report_request = cast(SubmitReportArguments, arguments)
            known = {record.call_id for record in self._records} | {
                execution.execution_id for execution in self._executions.values()
            }
            unknown = set(report_request.report.evidence_references) - known
            if unknown:
                raise GatewayError(
                    f"Report references {len(unknown)} unknown runtime evidence identifiers"
                )
            return ReportOutput(
                report_sha256=canonical_sha256(report_request.report),
                evidence_references=report_request.report.evidence_references,
            ), None
        raise GatewayRequestError(f"Unsupported tool: {tool_name}")

    def _list_tree(self, arguments: ListTreeArguments) -> TreeOutput:
        root = self._resolve(arguments.path, must_exist=True)
        if not root.is_dir():
            raise GatewayError("Tree path is not a directory")
        entries: list[TreeEntry] = []
        omitted = 0
        unrepresentable = 0
        truncated = False

        def visit(directory: Path, depth: int) -> None:
            nonlocal omitted, unrepresentable, truncated
            for child in sorted(directory.iterdir(), key=lambda item: item.name):
                if len(entries) >= arguments.max_entries:
                    truncated = True
                    return
                relative = self._relative(child)
                if self._is_sensitive(relative):
                    omitted += 1
                    continue
                if not self._is_representable(relative):
                    unrepresentable += 1
                    continue
                metadata = child.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    continue
                if child.is_dir():
                    entries.append(TreeEntry(path=relative, kind="directory"))
                    if depth < arguments.max_depth:
                        visit(child, depth + 1)
                elif child.is_file():
                    entries.append(
                        TreeEntry(path=relative, kind="file", size_bytes=metadata.st_size)
                    )

        visit(root, 0)
        return TreeOutput(
            entries=entries,
            truncated=truncated,
            omitted_unrepresentable=unrepresentable,
            omitted_sensitive=omitted,
        )

    def _search_code(self, arguments: SearchCodeArguments) -> SearchOutput:
        root = self._resolve(arguments.path, must_exist=True)
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        query = arguments.query if arguments.case_sensitive else arguments.query.casefold()
        matches: list[SearchMatch] = []
        skipped = 0
        truncated = False
        for candidate in candidates:
            if len(matches) >= arguments.max_results:
                truncated = True
                break
            if not candidate.is_file() or candidate.is_symlink():
                continue
            relative = self._relative(candidate)
            if (
                self._is_sensitive(relative)
                or not self._is_representable(relative)
                or candidate.stat().st_size > 1_000_000
            ):
                skipped += 1
                continue
            try:
                lines = candidate.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                skipped += 1
                continue
            for line_number, line in enumerate(lines, 1):
                candidate_line = line if arguments.case_sensitive else line.casefold()
                if query in candidate_line:
                    matches.append(SearchMatch(path=relative, line=line_number, text=line[:2000]))
                    if len(matches) >= arguments.max_results:
                        truncated = True
                        break
        return SearchOutput(
            matches=matches,
            truncated=truncated,
            skipped_files=skipped,
        )

    def _read_file_range(self, arguments: ReadFileRangeArguments) -> FileOutput:
        path = self._readable_file(arguments.path)
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise GatewayError("File is not UTF-8 text") from exc
        selected = text.splitlines(keepends=True)[arguments.start_line - 1 : arguments.end_line]
        content = "".join(selected)
        encoded = content.encode("utf-8")
        truncated = len(encoded) > arguments.max_output_bytes
        if truncated:
            content = encoded[: arguments.max_output_bytes].decode("utf-8", errors="ignore")
        return FileOutput(
            path=arguments.path,
            start_line=arguments.start_line,
            end_line=arguments.start_line + len(selected) - 1,
            content=content,
            content_sha256=sha256(raw).hexdigest(),
            truncated=truncated,
        )

    def _inspect_symbol(self, arguments: InspectSymbolArguments) -> SymbolOutput:
        path = self._readable_file(arguments.path)
        if path.suffix != ".py":
            raise GatewayError("Symbol inspection supports Python files only")
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise GatewayError("Python source cannot be parsed") from exc
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (
                node.name == arguments.symbol
            ):
                end_line = node.end_lineno or node.lineno
                symbol_kind: Literal["function", "async_function", "class"]
                if isinstance(node, ast.AsyncFunctionDef):
                    symbol_kind = "async_function"
                elif isinstance(node, ast.FunctionDef):
                    symbol_kind = "function"
                else:
                    symbol_kind = "class"
                lines = source.splitlines(keepends=True)
                return SymbolOutput(
                    path=arguments.path,
                    symbol=arguments.symbol,
                    symbol_kind=symbol_kind,
                    start_line=node.lineno,
                    end_line=end_line,
                    source="".join(lines[node.lineno - 1 : end_line]),
                )
        raise GatewayError("Symbol was not found")

    def _git_status(self) -> StatusOutput:
        raw = self.workspace_manager.status_porcelain(self.workspace)
        return StatusOutput(
            porcelain=raw.decode("utf-8", errors="strict").replace("\0", "\n"),
            clean=not raw,
        )

    def _git_diff(self, arguments: GitDiffArguments) -> DiffOutput:
        diff = self.workspace_manager.inspect_diff(self.workspace)
        patch = diff.patch[: arguments.max_output_bytes]
        return DiffOutput(
            base_sha=diff.base_sha,
            diff_sha256=diff.diff_sha256,
            patch=patch.decode("utf-8", errors="replace"),
            changed_files=list(diff.changed_files),
            binary_files=list(diff.binary_files),
            additions=diff.additions,
            deletions=diff.deletions,
            truncated=len(patch) != len(diff.patch),
        )

    def _inspect_diff(self) -> DiffInspectionOutput:
        diff = self.workspace_manager.inspect_diff(self.workspace)
        protected = [
            path for path in diff.changed_files if self._matches(path, self.profile.protected_paths)
        ]
        scope = [path for path in diff.changed_files if not self._in_scope(path)]
        tests = [path for path in diff.changed_files if self._is_test_path(path)]
        return DiffInspectionOutput(
            base_sha=diff.base_sha,
            diff_sha256=diff.diff_sha256,
            changed_files=list(diff.changed_files),
            binary_files=list(diff.binary_files),
            protected_path_violations=protected,
            scope_violations=scope,
            test_files_changed=tests,
            additions=diff.additions,
            deletions=diff.deletions,
        )

    def _write_patch(self, arguments: WritePatchArguments) -> MutationOutput:
        self._authorize_write(arguments.path)
        path = self._resolve(arguments.path, must_exist=True)
        if not path.is_file():
            raise GatewayError("Patch target is not a regular file")
        before = path.read_bytes()
        before_hash = sha256(before).hexdigest()
        if before_hash != arguments.expected_sha256:
            raise GatewayError("Patch target hash does not match")
        self._atomic_write(path, arguments.content.encode("utf-8"), preserve_mode=True)
        return MutationOutput(
            path=arguments.path,
            operation="updated",
            before_sha256=before_hash,
            after_sha256=sha256(arguments.content.encode("utf-8")).hexdigest(),
        )

    def _create_file(self, arguments: CreateFileArguments) -> MutationOutput:
        self._authorize_write(arguments.path)
        path = self._resolve(arguments.path, must_exist=False)
        if path.exists():
            raise GatewayError("Create target already exists")
        if not path.parent.is_dir():
            raise GatewayError("Create target parent does not exist")
        self._atomic_write(path, arguments.content.encode("utf-8"), preserve_mode=False)
        return MutationOutput(
            path=arguments.path,
            operation="created",
            after_sha256=sha256(arguments.content.encode("utf-8")).hexdigest(),
        )

    def _delete_file(self, arguments: DeleteFileArguments) -> MutationOutput:
        self._authorize_write(arguments.path)
        path = self._resolve(arguments.path, must_exist=True)
        if not path.is_file():
            raise GatewayError("Delete target is not a regular file")
        before_hash = sha256(path.read_bytes()).hexdigest()
        if before_hash != arguments.expected_sha256:
            raise GatewayError("Delete target hash does not match")
        path.unlink()
        return MutationOutput(
            path=arguments.path,
            operation="deleted",
            before_sha256=before_hash,
        )

    def _execute_command(
        self,
        purpose: CommandPurpose,
        phase: PatchForgePhase,
        call_id: UUID,
    ) -> SandboxExecution:
        command = self.profile.commands.get(purpose)
        if command is None:
            raise GatewayError(f"Repository profile has no {purpose} command")
        budget = self.policy.budgets.for_phase(phase)
        remaining = budget.max_duration_seconds - self._phase_duration.get(phase, 0.0)
        if command.timeout_seconds > remaining:
            raise GatewayError("Repository command does not fit the remaining phase duration")
        return self.sandbox.execute(
            SandboxRequest(
                run_id=self.identity.run_id,
                call_id=call_id,
                workspace=self.workspace.worktree,
                command=command,
                policy=self.profile.sandbox,
            )
        )

    @staticmethod
    def _command_purpose(tool_name: ToolName, phase: PatchForgePhase) -> CommandPurpose:
        return command_purpose_for(tool_name, phase)

    @staticmethod
    def _execution_output(
        purpose: CommandPurpose,
        execution: SandboxExecution,
    ) -> ExecutionOutput:
        return ExecutionOutput(
            execution_id=execution.execution_id,
            command_name=purpose.value,
            status=execution.status,
            exit_code=execution.exit_code,
            stdout=execution.stdout.decode("utf-8", errors="replace"),
            stderr=execution.stderr.decode("utf-8", errors="replace"),
            output_truncated=execution.output_truncated,
        )

    def _inspect_test_failure(
        self,
        arguments: InspectTestFailureArguments,
    ) -> ExecutionOutput:
        execution = self._executions.get(arguments.tool_call_id)
        if execution is None:
            raise GatewayError("Referenced execution tool call is unknown")
        if execution.status is SandboxStatus.SUCCEEDED:
            raise GatewayError("Referenced execution did not fail")
        stdout, stderr, truncated = _bounded_text_outputs(
            execution.stdout,
            execution.stderr,
            arguments.max_output_bytes,
        )
        return ExecutionOutput(
            execution_id=execution.execution_id,
            command_name="failure_inspection",
            status=execution.status,
            exit_code=execution.exit_code,
            stdout=stdout,
            stderr=stderr,
            output_truncated=execution.output_truncated or truncated,
        )

    @staticmethod
    def _status_for_execution(
        execution: SandboxExecution | None,
    ) -> tuple[ToolCallStatus, str | None]:
        if execution is None or execution.status is SandboxStatus.SUCCEEDED:
            return ToolCallStatus.SUCCEEDED, None
        if execution.status is SandboxStatus.TIMED_OUT:
            return ToolCallStatus.TIMED_OUT, "timeout"
        return ToolCallStatus.FAILED, execution.error_code or "sandbox_error"

    def _authorize_write(self, path: RepositoryPath) -> None:
        if self._is_sensitive(path):
            raise GatewayError("Sensitive paths cannot be modified")
        if path.rsplit("/", 1)[-1] in GIT_CONTROL_FILES:
            raise GatewayError("Git ignore and attribute files cannot be modified")
        if self._matches(path, self.profile.protected_paths):
            raise GatewayError("Protected paths cannot be modified")
        if not self._in_scope(path):
            raise GatewayError("Path is outside the engineering task scope")
        if not self.policy.allow_test_file_changes and self._is_test_path(path):
            raise GatewayError("Test-file changes are disabled by policy")
        if self.workspace_manager.is_ignored(self.workspace, path):
            raise GatewayError("Git-ignored paths cannot be modified because diffs would hide them")

    def _is_test_path(self, path: str) -> bool:
        return is_test_path(path, self.profile)

    @staticmethod
    def _is_representable(path: str) -> bool:
        try:
            return _REPOSITORY_PATH.validate_python(path) == path
        except ValidationError:
            return False

    def _in_scope(self, path: str) -> bool:
        return path_in_scope(path, self.task.scope_paths)

    @staticmethod
    def _matches(path: str, prefixes: list[RepositoryPath]) -> bool:
        return path_matches(path, prefixes)

    def _readable_file(self, path: RepositoryPath) -> Path:
        if self._is_sensitive(path):
            raise GatewayError("Sensitive paths cannot be read")
        resolved = self._resolve(path, must_exist=True)
        if not resolved.is_file():
            raise GatewayError("Read target is not a regular file")
        if resolved.stat().st_size > 1_000_000:
            raise GatewayError("Read target exceeds the file-size limit")
        return resolved

    def _resolve(self, relative: str, *, must_exist: bool) -> Path:
        root = self.workspace.worktree.resolve(strict=True)
        candidate = root if relative == "." else root.joinpath(*relative.split("/"))
        current = root
        for part in () if relative == "." else relative.split("/"):
            current = current / part
            if current.exists() and current.is_symlink():
                raise GatewayError("Symbolic links are not accessible through the gateway")
        try:
            resolved = candidate.resolve(strict=must_exist)
        except OSError as exc:
            raise GatewayError("Repository path does not exist") from exc
        if not resolved.is_relative_to(root):
            raise GatewayError("Repository path escaped the workspace")
        return resolved

    def _relative(self, path: Path) -> RepositoryPath:
        value = path.relative_to(self.workspace.worktree).as_posix()
        return value

    @staticmethod
    def _is_sensitive(path: str) -> bool:
        return is_sensitive_path(path)

    def _atomic_write(self, path: Path, content: bytes, *, preserve_mode: bool) -> None:
        temporary = path.parent / f".nexus-write-{self._id_factory().hex}.tmp"
        try:
            temporary.write_bytes(content)
            if preserve_mode:
                os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise GatewayRequestError("Gateway clock must be timezone-aware")
        return value


def _bounded_text_outputs(stdout: bytes, stderr: bytes, limit: int) -> tuple[str, str, bool]:
    combined = len(stdout) + len(stderr)
    stdout_part = stdout[:limit]
    stderr_part = stderr[: max(0, limit - len(stdout_part))]
    return (
        stdout_part.decode("utf-8", errors="replace"),
        stderr_part.decode("utf-8", errors="replace"),
        combined > limit,
    )


def _output_is_truncated(output: ToolOutput) -> bool:
    if isinstance(output, ExecutionOutput):
        return output.output_truncated
    if isinstance(output, (TreeOutput, SearchOutput, FileOutput, DiffOutput)):
        return output.truncated
    return False


def _failure_detail(error: Exception, fallback: str) -> str:
    return (str(error).strip() or fallback)[:500]
