"""Closed deterministic lifecycle for the PatchForge runtime."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from nexus.atlas.models import StrictModel
from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.gateway import (
    BUDGET_FAILURE_CODES,
    ExecutionOutput,
    FailureOutput,
    GatewayBudgetError,
    GatewayError,
    GatewayRequestError,
    GatewayResult,
    PhaseRequestOutput,
    ReportOutput,
    SubmitReportArguments,
)
from nexus.patchforge.models import (
    AgentReport,
    EngineeringTask,
    PatchForgeFailure,
    PatchForgePhase,
    PatchOutcome,
    RunBudgets,
    RunIdentity,
    ToolCallRecord,
    ToolName,
)
from nexus.patchforge.policy import PatchForgePolicy
from nexus.patchforge.sandbox import SandboxExecution, SandboxStatus
from nexus.patchforge.workspace import WorkspaceError, WorkspaceHandle

Clock = Callable[[], datetime]


class RuntimeTransitionError(ValueError):
    """Raised when the runtime lifecycle cannot accept a requested transition."""


class RuntimeCancelledError(RuntimeError):
    """Raised by an engine when an explicitly requested cancellation is observed."""


class RuntimeTransitionKind(StrEnum):
    PROGRESS = "progress"
    RETRY = "retry"
    FAILURE = "failure"
    REPORT = "report"
    CLEANUP = "cleanup"
    CLOSE = "close"


LIFECYCLE_TRANSITIONS: dict[PatchForgePhase, frozenset[PatchForgePhase]] = {
    PatchForgePhase.CREATED: frozenset({PatchForgePhase.PROVISIONING}),
    PatchForgePhase.PROVISIONING: frozenset({PatchForgePhase.RECON}),
    PatchForgePhase.RECON: frozenset({PatchForgePhase.HYPOTHESIS}),
    PatchForgePhase.HYPOTHESIS: frozenset({PatchForgePhase.REPRODUCE}),
    PatchForgePhase.REPRODUCE: frozenset({PatchForgePhase.IMPLEMENT}),
    PatchForgePhase.IMPLEMENT: frozenset({PatchForgePhase.TARGETED_VALIDATE}),
    PatchForgePhase.TARGETED_VALIDATE: frozenset(
        {PatchForgePhase.IMPLEMENT, PatchForgePhase.FULL_VALIDATE}
    ),
    PatchForgePhase.FULL_VALIDATE: frozenset({PatchForgePhase.SELF_REVIEW}),
    PatchForgePhase.SELF_REVIEW: frozenset({PatchForgePhase.FINALIZE}),
    PatchForgePhase.FINALIZE: frozenset({PatchForgePhase.REPORTED}),
    PatchForgePhase.REPORTED: frozenset({PatchForgePhase.CLEANUP}),
    PatchForgePhase.CLEANUP: frozenset({PatchForgePhase.CLOSED}),
    PatchForgePhase.CLOSED: frozenset(),
}

_FAILURE_OUTCOMES: dict[PatchForgeFailure, PatchOutcome] = {
    PatchForgeFailure.BUDGET_EXHAUSTED: PatchOutcome.PARTIAL,
    PatchForgeFailure.POLICY_DENIED: PatchOutcome.POLICY_VIOLATION,
    PatchForgeFailure.CANCELLED: PatchOutcome.CANCELLED,
    PatchForgeFailure.SANDBOX_ERROR: PatchOutcome.SANDBOX_FAILED,
    PatchForgeFailure.WORKSPACE_ERROR: PatchOutcome.ABORTED,
    PatchForgeFailure.ENGINE_ERROR: PatchOutcome.ABORTED,
    PatchForgeFailure.VALIDATION_FAILED: PatchOutcome.PARTIAL,
    PatchForgeFailure.ATTESTATION_FAILED: PatchOutcome.ABORTED,
    PatchForgeFailure.CLEANUP_FAILED: PatchOutcome.ABORTED,
}

_FAILURE_TO_FINALIZE = frozenset(
    {
        PatchForgePhase.CREATED,
        PatchForgePhase.PROVISIONING,
        PatchForgePhase.RECON,
        PatchForgePhase.HYPOTHESIS,
        PatchForgePhase.REPRODUCE,
        PatchForgePhase.IMPLEMENT,
        PatchForgePhase.TARGETED_VALIDATE,
        PatchForgePhase.FULL_VALIDATE,
        PatchForgePhase.SELF_REVIEW,
    }
)


class RuntimeTransition(StrictModel):
    run_id: UUID
    sequence: int = Field(ge=1)
    source: PatchForgePhase
    target: PatchForgePhase
    kind: RuntimeTransitionKind
    implementation_loops: int = Field(ge=0, le=20)
    failure: PatchForgeFailure | None = None
    occurred_at: AwareDatetime
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"

    @model_validator(mode="after")
    def validate_failure_shape(self) -> "RuntimeTransition":
        if (self.kind is RuntimeTransitionKind.FAILURE) != (self.failure is not None):
            raise ValueError("Only failure transitions carry a failure classification")
        return self


class RuntimeSnapshot(StrictModel):
    run_id: UUID
    phase: PatchForgePhase
    implementation_loops: int = Field(ge=0, le=20)
    max_implementation_loops: int = Field(ge=0, le=20)
    outcome: PatchOutcome | None = None
    failure: PatchForgeFailure | None = None
    cleanup_failed: bool = False
    transitions: list[RuntimeTransition] = Field(default_factory=list, max_length=100)
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"

    @model_validator(mode="after")
    def validate_transcript(self) -> "RuntimeSnapshot":
        if self.implementation_loops > self.max_implementation_loops:
            raise ValueError("Implementation loop count exceeds the configured limit")
        if (self.failure is None) != (self.outcome in {None, PatchOutcome.PATCH_PROPOSED}):
            raise ValueError("Runtime failure and outcome are inconsistent")
        expected_source = PatchForgePhase.CREATED
        retry_count = 0
        for sequence, transition in enumerate(self.transitions, start=1):
            if transition.sequence != sequence:
                raise ValueError("Runtime transition sequence must be contiguous")
            if transition.run_id != self.run_id:
                raise ValueError("Runtime transition belongs to another run")
            if transition.source is not expected_source:
                raise ValueError("Runtime transition transcript is not contiguous")
            expected_source = transition.target
            if transition.kind is RuntimeTransitionKind.RETRY:
                retry_count += 1
        if expected_source is not self.phase:
            raise ValueError("Runtime phase does not match its transition transcript")
        if retry_count != self.implementation_loops:
            raise ValueError("Implementation loop count does not match retry transitions")
        return self


class RuntimeToolAction(StrictModel):
    """One model-authored tool request; parallel action batches do not exist in v1."""

    tool_name: ToolName
    arguments: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)


class RuntimeTurn(StrictModel):
    run_id: UUID
    phase: PatchForgePhase
    task: EngineeringTask
    previous_result: GatewayResult | None = None
    failure: PatchForgeFailure | None = None
    implementation_loops: int = Field(ge=0, le=20)
    max_implementation_loops: int = Field(ge=0, le=20)


class LeaseRenewalRecord(StrictModel):
    run_id: UUID
    sequence: int = Field(ge=1)
    workspace_id: UUID
    previous_expires_at: AwareDatetime
    renewed_expires_at: AwareDatetime
    refreshed_gateway: Literal[True] = True
    occurred_at: AwareDatetime
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"

    @model_validator(mode="after")
    def validate_expiry(self) -> "LeaseRenewalRecord":
        if self.renewed_expires_at < self.previous_expires_at:
            raise ValueError("Workspace lease renewal cannot shorten the active lease")
        return self


class RuntimeCompletion(StrictModel):
    identity: RunIdentity
    snapshot: RuntimeSnapshot
    report: AgentReport | None = None
    gateway_results: list[GatewayResult] = Field(default_factory=list, max_length=10_000)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list, max_length=10_000)
    executions: list[SandboxExecution] = Field(default_factory=list, max_length=1000)
    lease_renewals: list[LeaseRenewalRecord] = Field(default_factory=list, max_length=10_000)
    workspace_cleaned: bool
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"

    @model_validator(mode="after")
    def validate_completion(self) -> "RuntimeCompletion":
        if self.snapshot.run_id != self.identity.run_id:
            raise ValueError("Runtime snapshot belongs to another run")
        if self.snapshot.phase is not PatchForgePhase.CLOSED:
            raise ValueError("Runtime completion requires a closed lifecycle")
        if self.snapshot.cleanup_failed == self.workspace_cleaned:
            raise ValueError("Workspace cleanup result does not match the lifecycle")
        if any(item.record.run_id != self.identity.run_id for item in self.gateway_results):
            raise ValueError("Gateway result belongs to another run")
        if [item.record for item in self.gateway_results] != self.tool_calls:
            raise ValueError("Runtime tool calls must match ordered gateway results")
        if any(item.run_id != self.identity.run_id for item in self.executions):
            raise ValueError("Sandbox execution belongs to another run")
        if any(item.run_id != self.identity.run_id for item in self.lease_renewals):
            raise ValueError("Lease renewal belongs to another run")
        report_outputs = [
            item.output for item in self.gateway_results if isinstance(item.output, ReportOutput)
        ]
        if (self.report is None) != (len(report_outputs) == 0):
            raise ValueError("Runtime report does not match gateway report evidence")
        if len(report_outputs) > 1:
            raise ValueError("Runtime can accept only one report")
        if self.report is not None:
            output = report_outputs[0]
            if (
                output.report_sha256 != canonical_sha256(self.report)
                or output.evidence_references != self.report.evidence_references
            ):
                raise ValueError("Runtime report does not match its gateway attestation")
        return self


@runtime_checkable
class RuntimeEngine(Protocol):
    """Single-action reasoning boundary; production model execution is deferred."""

    def next_action(self, turn: RuntimeTurn) -> RuntimeToolAction: ...


@runtime_checkable
class RuntimeGateway(Protocol):
    identity: RunIdentity
    task: EngineeringTask
    policy: PatchForgePolicy
    workspace: WorkspaceHandle

    @property
    def records(self) -> tuple[ToolCallRecord, ...]: ...

    @property
    def executions(self) -> tuple[SandboxExecution, ...]: ...

    @staticmethod
    def requires_workspace(tool_name: ToolName) -> bool: ...

    def invoke(
        self,
        tool_name: ToolName,
        arguments: Mapping[str, object],
        *,
        phase: PatchForgePhase,
    ) -> GatewayResult: ...

    def refresh_workspace(self, workspace: WorkspaceHandle) -> None: ...


@runtime_checkable
class RuntimeWorkspaceManager(Protocol):
    def renew_lease(
        self,
        handle: WorkspaceHandle,
        lease_duration: timedelta,
    ) -> WorkspaceHandle: ...

    def cleanup(self, workspace_id: UUID) -> bool: ...


def require_lifecycle_transition(
    current: PatchForgePhase,
    target: PatchForgePhase,
) -> None:
    """Reject skipped, reversed, implicit, or terminal lifecycle transitions."""

    if target not in LIFECYCLE_TRANSITIONS[current]:
        raise RuntimeTransitionError(
            f"Invalid PatchForge runtime transition: {current} -> {target}"
        )


class PatchForgeLifecycle:
    """Mutable lifecycle controller with an immutable typed transcript."""

    def __init__(
        self,
        *,
        identity: RunIdentity,
        budgets: RunBudgets,
        clock: Clock | None = None,
    ) -> None:
        self._run_id = identity.run_id
        self._max_implementation_loops = budgets.max_implementation_loops
        self._clock = clock or (lambda: datetime.now(UTC))
        self._phase = PatchForgePhase.CREATED
        self._implementation_loops = 0
        self._outcome: PatchOutcome | None = None
        self._failure: PatchForgeFailure | None = None
        self._cleanup_failed = False
        self._transitions: list[RuntimeTransition] = []

    @property
    def phase(self) -> PatchForgePhase:
        return self._phase

    @property
    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            run_id=self._run_id,
            phase=self._phase,
            implementation_loops=self._implementation_loops,
            max_implementation_loops=self._max_implementation_loops,
            outcome=self._outcome,
            failure=self._failure,
            cleanup_failed=self._cleanup_failed,
            transitions=list(self._transitions),
        )

    def advance(self, target: PatchForgePhase) -> RuntimeTransition:
        if self._failure is not None:
            raise RuntimeTransitionError("A failed runtime cannot resume the success path")
        if self._phase is PatchForgePhase.FINALIZE:
            raise RuntimeTransitionError("Finalization requires an explicit report transition")
        if self._phase in {PatchForgePhase.REPORTED, PatchForgePhase.CLEANUP}:
            raise RuntimeTransitionError("Cleanup transitions require explicit runtime methods")
        require_lifecycle_transition(self._phase, target)
        kind = RuntimeTransitionKind.PROGRESS
        if self._phase is PatchForgePhase.TARGETED_VALIDATE and target is PatchForgePhase.IMPLEMENT:
            if self._implementation_loops >= self._max_implementation_loops:
                raise RuntimeTransitionError("Implementation loop budget is exhausted")
            self._implementation_loops += 1
            kind = RuntimeTransitionKind.RETRY
        return self._record(target, kind)

    def fail(self, failure: PatchForgeFailure) -> RuntimeTransition:
        if self._failure is not None:
            raise RuntimeTransitionError("Runtime failure is already classified")
        if failure is PatchForgeFailure.CLEANUP_FAILED:
            raise RuntimeTransitionError("Cleanup failure requires the cleanup phase")
        if self._phase in _FAILURE_TO_FINALIZE:
            target = PatchForgePhase.FINALIZE
        elif self._phase is PatchForgePhase.FINALIZE:
            target = PatchForgePhase.CLEANUP
        else:
            raise RuntimeTransitionError(f"Runtime failure cannot start from {self._phase}")
        self._failure = failure
        self._outcome = _FAILURE_OUTCOMES[failure]
        return self._record(target, RuntimeTransitionKind.FAILURE, failure=failure)

    def report(self) -> RuntimeTransition:
        if self._phase is not PatchForgePhase.FINALIZE:
            raise RuntimeTransitionError("A report can be recorded only from finalization")
        return self._record(PatchForgePhase.REPORTED, RuntimeTransitionKind.REPORT)

    def finalization_failed(self, failure: PatchForgeFailure) -> RuntimeTransition:
        if self._phase is not PatchForgePhase.FINALIZE:
            raise RuntimeTransitionError("Finalization failure requires the finalization phase")
        if self._failure is None:
            self._failure = failure
            self._outcome = _FAILURE_OUTCOMES[failure]
        return self._record(
            PatchForgePhase.CLEANUP,
            RuntimeTransitionKind.FAILURE,
            failure=failure,
        )

    def begin_cleanup(self) -> RuntimeTransition:
        if self._phase is not PatchForgePhase.REPORTED:
            raise RuntimeTransitionError("Cleanup can begin only after a report")
        return self._record(PatchForgePhase.CLEANUP, RuntimeTransitionKind.CLEANUP)

    def close(self) -> RuntimeTransition:
        if self._phase is not PatchForgePhase.CLEANUP:
            raise RuntimeTransitionError("Runtime can close only after cleanup")
        return self._record(PatchForgePhase.CLOSED, RuntimeTransitionKind.CLOSE)

    def cleanup_failed(self) -> RuntimeTransition:
        if self._phase is not PatchForgePhase.CLEANUP:
            raise RuntimeTransitionError("Cleanup failure can be recorded only during cleanup")
        if self._failure is None:
            self._failure = PatchForgeFailure.CLEANUP_FAILED
            self._outcome = PatchOutcome.ABORTED
        self._cleanup_failed = True
        return self._record(
            PatchForgePhase.CLOSED,
            RuntimeTransitionKind.FAILURE,
            failure=PatchForgeFailure.CLEANUP_FAILED,
        )

    def _record(
        self,
        target: PatchForgePhase,
        kind: RuntimeTransitionKind,
        *,
        failure: PatchForgeFailure | None = None,
    ) -> RuntimeTransition:
        transition = RuntimeTransition(
            run_id=self._run_id,
            sequence=len(self._transitions) + 1,
            source=self._phase,
            target=target,
            kind=kind,
            implementation_loops=self._implementation_loops,
            failure=failure,
            occurred_at=self._clock(),
        )
        self._transitions.append(transition)
        self._phase = target
        return transition


class PatchForgeRuntime:
    """Coordinate one bounded run exclusively through the typed ToolGateway."""

    def __init__(
        self,
        *,
        gateway: RuntimeGateway,
        workspace_manager: RuntimeWorkspaceManager,
        engine: RuntimeEngine,
        lease_duration: timedelta,
        clock: Clock | None = None,
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("Runtime workspace lease duration must be positive")
        if gateway.identity.run_id != gateway.workspace.record.run_id:
            raise ValueError("Runtime gateway workspace belongs to another run")
        if gateway.policy.parallel_tool_calls is not False:
            raise ValueError("PatchForge Runtime requires disabled parallel tool calls")
        if gateway.policy.memory_enabled is not False:
            raise ValueError("PatchForge Runtime v1 cannot enable memory")
        self.gateway = gateway
        self.workspace_manager = workspace_manager
        self.engine = engine
        self.lease_duration = lease_duration
        self._clock = clock or (lambda: datetime.now(UTC))
        self.lifecycle = PatchForgeLifecycle(
            identity=gateway.identity,
            budgets=gateway.policy.budgets,
            clock=self._clock,
        )
        self._results: list[GatewayResult] = []
        self._renewals: list[LeaseRenewalRecord] = []
        self._report: AgentReport | None = None
        self._workspace_cleaned = False

    def execute(self) -> RuntimeCompletion:
        previous_result: GatewayResult | None = None
        try:
            self.lifecycle.advance(PatchForgePhase.PROVISIONING)
            self.lifecycle.advance(PatchForgePhase.RECON)
            while self.lifecycle.phase not in {
                PatchForgePhase.REPORTED,
                PatchForgePhase.CLEANUP,
                PatchForgePhase.CLOSED,
            }:
                snapshot = self.lifecycle.snapshot
                turn = RuntimeTurn(
                    run_id=self.gateway.identity.run_id,
                    phase=snapshot.phase,
                    task=self.gateway.task,
                    previous_result=previous_result,
                    failure=snapshot.failure,
                    implementation_loops=snapshot.implementation_loops,
                    max_implementation_loops=snapshot.max_implementation_loops,
                )
                try:
                    action = self.engine.next_action(turn)
                    if self.gateway.requires_workspace(action.tool_name):
                        self._renew_workspace()
                    result = self.gateway.invoke(
                        action.tool_name,
                        action.arguments,
                        phase=self.lifecycle.phase,
                    )
                    self._results.append(result)
                    previous_result = result
                    self._accept_result(action, result)
                except RuntimeCancelledError:
                    self._record_failure(PatchForgeFailure.CANCELLED)
                except GatewayBudgetError:
                    self._record_failure(PatchForgeFailure.BUDGET_EXHAUSTED)
                except (GatewayRequestError, RuntimeTransitionError) as exc:
                    failure = (
                        PatchForgeFailure.BUDGET_EXHAUSTED
                        if "loop budget" in str(exc)
                        else PatchForgeFailure.POLICY_DENIED
                    )
                    self._record_failure(failure)
                except WorkspaceError:
                    self._record_failure(PatchForgeFailure.WORKSPACE_ERROR)
                except GatewayError:
                    self._record_failure(PatchForgeFailure.POLICY_DENIED)
                except Exception:
                    self._record_failure(PatchForgeFailure.ENGINE_ERROR)
        finally:
            self._finish_cleanup()
        return RuntimeCompletion(
            identity=self.gateway.identity,
            snapshot=self.lifecycle.snapshot,
            report=self._report,
            gateway_results=self._results,
            tool_calls=list(self.gateway.records),
            executions=list(self.gateway.executions),
            lease_renewals=self._renewals,
            workspace_cleaned=self._workspace_cleaned,
        )

    def _renew_workspace(self) -> None:
        current = self.gateway.workspace
        renewed = self.workspace_manager.renew_lease(current, self.lease_duration)
        try:
            self.gateway.refresh_workspace(renewed)
        except GatewayError as exc:
            raise WorkspaceError("Renewed workspace failed gateway refresh") from exc
        self._renewals.append(
            LeaseRenewalRecord(
                run_id=self.gateway.identity.run_id,
                sequence=len(self._renewals) + 1,
                workspace_id=renewed.record.workspace_id,
                previous_expires_at=current.record.lease_expires_at,
                renewed_expires_at=renewed.record.lease_expires_at,
                occurred_at=self._clock(),
            )
        )

    def _accept_result(self, action: RuntimeToolAction, result: GatewayResult) -> None:
        output = result.output
        if isinstance(output, FailureOutput):
            self._record_failure(self._classify_failure(output))
            return
        if isinstance(output, ExecutionOutput):
            if output.status in {SandboxStatus.TIMED_OUT, SandboxStatus.OUTPUT_LIMIT}:
                self._record_failure(PatchForgeFailure.BUDGET_EXHAUSTED)
            elif output.status is SandboxStatus.SANDBOX_ERROR:
                self._record_failure(PatchForgeFailure.SANDBOX_ERROR)
            return
        if isinstance(output, PhaseRequestOutput):
            if output.current_phase is not self.lifecycle.phase:
                raise RuntimeTransitionError("Phase request does not match runtime state")
            self.lifecycle.advance(output.target_phase)
            return
        if isinstance(output, ReportOutput):
            if action.tool_name is not ToolName.SUBMIT_REPORT:
                raise RuntimeTransitionError("Report evidence came from another tool")
            request = SubmitReportArguments.model_validate(action.arguments)
            self._report = request.report
            self.lifecycle.report()

    @staticmethod
    def _classify_failure(output: FailureOutput) -> PatchForgeFailure:
        if output.code in BUDGET_FAILURE_CODES:
            return PatchForgeFailure.BUDGET_EXHAUSTED
        if output.code == "policy_denied":
            return PatchForgeFailure.POLICY_DENIED
        if output.code == "sandbox_error":
            return PatchForgeFailure.SANDBOX_ERROR
        if output.code == "tool_error":
            return PatchForgeFailure.WORKSPACE_ERROR
        return PatchForgeFailure.ENGINE_ERROR

    def _record_failure(self, failure: PatchForgeFailure) -> None:
        if self.lifecycle.phase is PatchForgePhase.FINALIZE:
            self.lifecycle.finalization_failed(failure)
        elif self.lifecycle.phase not in {
            PatchForgePhase.CLEANUP,
            PatchForgePhase.CLOSED,
            PatchForgePhase.REPORTED,
        }:
            self.lifecycle.fail(failure)

    def _finish_cleanup(self) -> None:
        phase = self.lifecycle.phase
        if phase is PatchForgePhase.REPORTED:
            self.lifecycle.begin_cleanup()
        elif phase is PatchForgePhase.FINALIZE:
            self.lifecycle.finalization_failed(PatchForgeFailure.ENGINE_ERROR)
        elif phase not in {PatchForgePhase.CLEANUP, PatchForgePhase.CLOSED}:
            self._record_failure(PatchForgeFailure.ENGINE_ERROR)
            if self.lifecycle.phase is PatchForgePhase.FINALIZE:
                self.lifecycle.finalization_failed(PatchForgeFailure.ENGINE_ERROR)
        if self.lifecycle.phase is not PatchForgePhase.CLEANUP:
            return
        try:
            self.workspace_manager.cleanup(self.gateway.workspace.record.workspace_id)
        except (WorkspaceError, OSError):
            self.lifecycle.cleanup_failed()
        else:
            self._workspace_cleaned = True
            self.lifecycle.close()
