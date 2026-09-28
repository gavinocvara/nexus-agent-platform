"""Closed deterministic lifecycle for the PatchForge runtime."""

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from nexus.atlas.models import StrictModel
from nexus.patchforge.models import (
    PatchForgeFailure,
    PatchForgePhase,
    PatchOutcome,
    RunBudgets,
    RunIdentity,
)

Clock = Callable[[], datetime]


class RuntimeTransitionError(ValueError):
    """Raised when the runtime lifecycle cannot accept a requested transition."""


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
        if self._failure is None:
            self._outcome = PatchOutcome.PATCH_PROPOSED
        return self._record(PatchForgePhase.REPORTED, RuntimeTransitionKind.REPORT)

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
