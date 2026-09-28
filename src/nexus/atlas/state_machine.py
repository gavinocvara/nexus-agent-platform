"""Closed deterministic lifecycle for Atlas jobs."""

from nexus.atlas.models import JobStatus


class InvalidTransitionError(ValueError):
    """Raised when a caller attempts a transition outside the closed graph."""


VALID_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.CREATED: frozenset({JobStatus.VALIDATED, JobStatus.FAILED}),
    JobStatus.VALIDATED: frozenset({JobStatus.QUEUED, JobStatus.FAILED}),
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset({JobStatus.AWAITING_REVIEW, JobStatus.QUEUED, JobStatus.FAILED}),
    JobStatus.AWAITING_REVIEW: frozenset(
        {JobStatus.APPROVED, JobStatus.REJECTED, JobStatus.FAILED}
    ),
    JobStatus.APPROVED: frozenset({JobStatus.COMPLETED, JobStatus.FAILED}),
    JobStatus.REJECTED: frozenset({JobStatus.FAILED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
}


def require_transition(current: JobStatus, target: JobStatus) -> None:
    """Reject implicit, skipped, reversed, or terminal transitions."""

    if target not in VALID_TRANSITIONS[current]:
        raise InvalidTransitionError(f"Invalid Atlas job transition: {current} -> {target}")
