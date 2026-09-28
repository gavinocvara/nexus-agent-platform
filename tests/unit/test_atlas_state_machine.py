"""Exhaustive Atlas lifecycle graph coverage."""

import pytest

from nexus.atlas.models import JobStatus
from nexus.atlas.state_machine import VALID_TRANSITIONS, InvalidTransitionError, require_transition

EXPECTED_TRANSITIONS = {
    (JobStatus.CREATED, JobStatus.VALIDATED),
    (JobStatus.CREATED, JobStatus.FAILED),
    (JobStatus.VALIDATED, JobStatus.QUEUED),
    (JobStatus.VALIDATED, JobStatus.FAILED),
    (JobStatus.QUEUED, JobStatus.RUNNING),
    (JobStatus.QUEUED, JobStatus.FAILED),
    (JobStatus.RUNNING, JobStatus.AWAITING_REVIEW),
    (JobStatus.RUNNING, JobStatus.QUEUED),
    (JobStatus.RUNNING, JobStatus.FAILED),
    (JobStatus.AWAITING_REVIEW, JobStatus.APPROVED),
    (JobStatus.AWAITING_REVIEW, JobStatus.REJECTED),
    (JobStatus.AWAITING_REVIEW, JobStatus.FAILED),
    (JobStatus.APPROVED, JobStatus.COMPLETED),
    (JobStatus.APPROVED, JobStatus.FAILED),
    (JobStatus.REJECTED, JobStatus.FAILED),
}


def test_transition_table_is_complete_and_exact() -> None:
    actual = {
        (source, target) for source, targets in VALID_TRANSITIONS.items() for target in targets
    }
    assert set(VALID_TRANSITIONS) == set(JobStatus)
    assert actual == EXPECTED_TRANSITIONS


@pytest.mark.parametrize("source", list(JobStatus))
@pytest.mark.parametrize("target", list(JobStatus))
def test_every_valid_and_invalid_transition(source: JobStatus, target: JobStatus) -> None:
    if (source, target) in EXPECTED_TRANSITIONS:
        require_transition(source, target)
    else:
        with pytest.raises(InvalidTransitionError, match="Invalid Atlas job transition"):
            require_transition(source, target)


@pytest.mark.parametrize(
    "terminal",
    [JobStatus.COMPLETED, JobStatus.FAILED],
)
def test_terminal_states_have_no_exit(terminal: JobStatus) -> None:
    assert VALID_TRANSITIONS[terminal] == frozenset()
