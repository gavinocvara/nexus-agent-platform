"""End-to-end deterministic command tests for the Atlas thin control plane."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from nexus.atlas.models import (
    ActorIdentity,
    ActorType,
    AgentPolicy,
    ApprovalState,
    ArtifactReference,
    AuditEventType,
    Capability,
    CheckResult,
    ExecutionBudget,
    FailureClassification,
    JobSpec,
    JobStatus,
    PatchResult,
    ReviewResult,
    ReviewVerdict,
    SourceRevision,
    TaskRequest,
)
from nexus.atlas.policy import AgentRegistry, AtlasPolicyError
from nexus.atlas.protocols import JobDispatch
from nexus.atlas.service import AtlasCommandError, AtlasControlPlane
from nexus.atlas.state_machine import InvalidTransitionError
from nexus.atlas.storage import ConcurrencyError, IdempotencyConflictError, SQLiteAtlasStore

SYSTEM = ActorIdentity(actor_type=ActorType.SYSTEM, actor_id="atlas.system")
HUMAN = ActorIdentity(actor_type=ActorType.HUMAN, actor_id="owner.reviewer")
PATCHFORGE = ActorIdentity(actor_type=ActorType.AGENT, actor_id="patchforge.engineer")
SENTINEL = ActorIdentity(actor_type=ActorType.AGENT, actor_id="sentinelqa.reviewer")
START = datetime(2026, 9, 27, 12, tzinfo=UTC)


class MutableClock:
    def __init__(self) -> None:
        self.value = START

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class SequentialIds:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(int=self.value)


def _budget() -> ExecutionBudget:
    return ExecutionBudget(
        max_duration_seconds=60,
        max_tool_calls=20,
        max_input_tokens=50_000,
        max_output_tokens=10_000,
        max_artifact_bytes=5_000_000,
    )


def _registry() -> AgentRegistry:
    return AgentRegistry(
        [
            AgentPolicy(
                agent_id=PATCHFORGE.actor_id,
                runtime_kind="patchforge",
                runtime_version="v1",
                capabilities=[
                    Capability.SOURCE_READ,
                    Capability.WORKTREE_WRITE,
                    Capability.TEST_EXECUTE,
                    Capability.PATCH_CREATE,
                ],
                max_budget=_budget(),
                repository_prefixes=["https://github.com/gavinocvara/"],
            ),
            AgentPolicy(
                agent_id=SENTINEL.actor_id,
                runtime_kind="sentinelqa",
                runtime_version="v1",
                capabilities=[Capability.REVIEW_READ, Capability.REVIEW_SUBMIT],
                max_budget=_budget(),
                repository_prefixes=["https://github.com/gavinocvara/"],
            ),
        ]
    )


def _spec(**updates: object) -> JobSpec:
    values = {
        "agent_id": PATCHFORGE.actor_id,
        "reviewer_agent_id": SENTINEL.actor_id,
        "request": TaskRequest(
            task_type="software_change",
            title="Implement a bounded change",
            instructions="Modify only the requested repository behavior.",
            acceptance_criteria=["Tests pass", "No unrestricted shell capability"],
        ),
        "source": SourceRevision(
            repository_url="https://github.com/gavinocvara/nexus-agent-platform",
            commit_sha="1" * 40,
            ref="main",
        ),
        "budget": _budget(),
        "capabilities": [
            Capability.SOURCE_READ,
            Capability.WORKTREE_WRITE,
            Capability.TEST_EXECUTE,
            Capability.PATCH_CREATE,
        ],
    }
    values.update(updates)
    return JobSpec.model_validate(values)


def _patch_result(base_sha: str = "1" * 40) -> PatchResult:
    return PatchResult(
        summary="Prepared a bounded patch and test evidence.",
        base_sha=base_sha,
        proposed_head_sha="2" * 40,
        patch_artifact=ArtifactReference(
            artifact_type="unified_diff",
            uri="atlas://jobs/1/patch.diff",
            sha256="a" * 64,
        ),
        changed_files=["src/nexus/example.py", "tests/unit/test_example.py"],
        checks=[CheckResult(name="pytest", passed=True, summary="Tests passed.")],
    )


def _control(tmp_path: Path) -> tuple[AtlasControlPlane, SQLiteAtlasStore, MutableClock]:
    clock = MutableClock()
    store = SQLiteAtlasStore(tmp_path / "atlas.sqlite3")
    control = AtlasControlPlane(store, _registry(), clock=clock, id_factory=SequentialIds())
    return control, store, clock


def _through_running(control: AtlasControlPlane):  # type: ignore[no-untyped-def]
    created = control.create_job(_spec(), command_id="command:create:001", actor=SYSTEM)
    validated = control.validate_job(
        created.job_id,
        command_id="command:validate:001",
        expected_revision=0,
        actor=SYSTEM,
    )
    queued = control.queue_job(
        created.job_id,
        command_id="command:queue:001",
        expected_revision=validated.revision,
        actor=SYSTEM,
    )
    running = control.start_job(
        created.job_id,
        command_id="command:start:001",
        expected_revision=queued.revision,
        actor=PATCHFORGE,
    )
    dispatch = JobDispatch.from_job(running)
    assert dispatch.execution_id == running.active_execution.execution_id
    assert dispatch.capabilities == running.capabilities
    return created, validated, queued, running


def _through_review(control: AtlasControlPlane, clock: MutableClock):  # type: ignore[no-untyped-def]
    created, _, _, running = _through_running(control)
    awaiting = control.submit_result(
        created.job_id,
        _patch_result(),
        command_id="command:result:001",
        expected_revision=running.revision,
        actor=PATCHFORGE,
    )
    review = ReviewResult(
        review_id=UUID(int=100),
        reviewer_agent_id=SENTINEL.actor_id,
        verdict=ReviewVerdict.PASSED,
        summary="The patch meets the supplied acceptance criteria.",
        created_at=clock(),
    )
    reviewed = control.record_review(
        created.job_id,
        review,
        command_id="command:review:001",
        expected_revision=awaiting.revision,
        actor=SENTINEL,
    )
    return created, reviewed


def test_patchforge_sentinel_human_flow_is_explicit_and_fully_audited(tmp_path: Path) -> None:
    control, store, clock = _control(tmp_path)
    created, reviewed = _through_review(control, clock)
    approved = control.approve_job(
        created.job_id,
        "Human review accepts the verified patch result.",
        command_id="command:approve:001",
        expected_revision=reviewed.revision,
        actor=HUMAN,
    )
    completed = control.complete_job(
        created.job_id,
        command_id="command:complete:001",
        expected_revision=approved.revision,
        actor=SYSTEM,
    )

    assert completed.status is JobStatus.COMPLETED
    assert completed.approval_state is ApprovalState.APPROVED
    assert isinstance(completed.result, PatchResult)
    assert completed.review is not None
    assert completed.approval is not None
    assert completed.revision == 7
    events = store.list_audit_events(created.job_id)
    assert [event.sequence for event in events] == list(range(1, 9))
    assert [event.event_type for event in events] == [
        AuditEventType.JOB_CREATED,
        AuditEventType.JOB_VALIDATED,
        AuditEventType.JOB_QUEUED,
        AuditEventType.JOB_STARTED,
        AuditEventType.RESULT_RECORDED,
        AuditEventType.REVIEW_RECORDED,
        AuditEventType.JOB_APPROVED,
        AuditEventType.JOB_COMPLETED,
    ]
    assert all(event.job_revision == event.sequence - 1 for event in events)


def test_human_cannot_approve_without_review_or_after_failed_review(tmp_path: Path) -> None:
    control, store, clock = _control(tmp_path)
    created, _, _, running = _through_running(control)
    awaiting = control.submit_result(
        created.job_id,
        _patch_result(),
        command_id="command:result:001",
        expected_revision=running.revision,
        actor=PATCHFORGE,
    )
    with pytest.raises(AtlasCommandError, match="recorded reviewer"):
        control.approve_job(
            created.job_id,
            "Premature approval.",
            command_id="command:approve:bad1",
            expected_revision=awaiting.revision,
            actor=HUMAN,
        )
    failed_review = ReviewResult(
        review_id=UUID(int=100),
        reviewer_agent_id=SENTINEL.actor_id,
        verdict=ReviewVerdict.FAILED,
        summary="Tests do not establish the requested behavior.",
        created_at=clock(),
    )
    reviewed = control.record_review(
        created.job_id,
        failed_review,
        command_id="command:review:001",
        expected_revision=awaiting.revision,
        actor=SENTINEL,
    )
    with pytest.raises(AtlasCommandError, match="passed reviewer"):
        control.approve_job(
            created.job_id,
            "Override failed review.",
            command_id="command:approve:bad2",
            expected_revision=reviewed.revision,
            actor=HUMAN,
        )
    assert len(store.list_audit_events(created.job_id)) == 6


def test_human_rejection_requires_explicit_failed_closeout(tmp_path: Path) -> None:
    control, store, clock = _control(tmp_path)
    created, reviewed = _through_review(control, clock)
    rejected = control.reject_job(
        created.job_id,
        "Human reviewer rejects this change.",
        command_id="command:reject:001",
        expected_revision=reviewed.revision,
        actor=HUMAN,
    )
    assert rejected.status is JobStatus.REJECTED
    assert rejected.terminal_at is None
    with pytest.raises(InvalidTransitionError):
        control.complete_job(
            created.job_id,
            command_id="command:complete:bad",
            expected_revision=rejected.revision,
            actor=SYSTEM,
        )
    failed = control.fail_job(
        created.job_id,
        FailureClassification.REVIEW_FAILED,
        "Human approval was rejected after review.",
        retryable=False,
        command_id="command:fail:001",
        expected_revision=rejected.revision,
        actor=SYSTEM,
    )
    assert failed.status is JobStatus.FAILED
    assert failed.terminal_at is not None
    assert [event.event_type for event in store.list_audit_events(created.job_id)][-2:] == [
        AuditEventType.JOB_REJECTED,
        AuditEventType.JOB_FAILED,
    ]


def test_commands_are_idempotent_and_conflicting_reuse_fails(tmp_path: Path) -> None:
    control, _, _ = _control(tmp_path)
    first = control.create_job(_spec(), command_id="command:create:001", actor=SYSTEM)
    replay = control.create_job(_spec(), command_id="command:create:001", actor=SYSTEM)
    assert replay == first
    with pytest.raises(IdempotencyConflictError):
        control.create_job(
            _spec(
                request=TaskRequest(
                    task_type="software_change",
                    title="Different task",
                    instructions="Different input under the same command ID.",
                )
            ),
            command_id="command:create:001",
            actor=SYSTEM,
        )

    validated = control.validate_job(
        first.job_id,
        command_id="command:validate:001",
        expected_revision=0,
        actor=SYSTEM,
    )
    control.queue_job(
        first.job_id,
        command_id="command:queue:001",
        expected_revision=validated.revision,
        actor=SYSTEM,
    )
    old_response = control.validate_job(
        first.job_id,
        command_id="command:validate:001",
        expected_revision=0,
        actor=SYSTEM,
    )
    assert old_response.status is JobStatus.VALIDATED
    assert old_response.revision == 1


def test_stale_revision_and_invalid_transition_do_not_append_events(tmp_path: Path) -> None:
    control, store, _ = _control(tmp_path)
    created = control.create_job(_spec(), command_id="command:create:001", actor=SYSTEM)
    with pytest.raises(InvalidTransitionError):
        control.queue_job(
            created.job_id,
            command_id="command:queue:bad1",
            expected_revision=0,
            actor=SYSTEM,
        )
    validated = control.validate_job(
        created.job_id,
        command_id="command:validate:001",
        expected_revision=0,
        actor=SYSTEM,
    )
    with pytest.raises(ConcurrencyError, match="expected revision"):
        control.queue_job(
            created.job_id,
            command_id="command:queue:bad2",
            expected_revision=0,
            actor=SYSTEM,
        )
    assert validated.revision == 1
    assert len(store.list_audit_events(created.job_id)) == 2


def test_agent_identity_and_patch_base_are_enforced(tmp_path: Path) -> None:
    control, store, _ = _control(tmp_path)
    created = control.create_job(_spec(), command_id="command:create:001", actor=SYSTEM)
    validated = control.validate_job(
        created.job_id,
        command_id="command:validate:001",
        expected_revision=0,
        actor=SYSTEM,
    )
    queued = control.queue_job(
        created.job_id,
        command_id="command:queue:001",
        expected_revision=validated.revision,
        actor=SYSTEM,
    )
    with pytest.raises(AtlasPolicyError, match="assigned agent"):
        control.start_job(
            created.job_id,
            command_id="command:start:bad1",
            expected_revision=queued.revision,
            actor=SENTINEL,
        )
    running = control.start_job(
        created.job_id,
        command_id="command:start:001",
        expected_revision=queued.revision,
        actor=PATCHFORGE,
    )
    with pytest.raises(AtlasCommandError, match="base SHA"):
        control.submit_result(
            created.job_id,
            _patch_result("3" * 40),
            command_id="command:result:bad1",
            expected_revision=running.revision,
            actor=PATCHFORGE,
        )
    assert len(store.list_audit_events(created.job_id)) == 4


def test_expired_execution_is_explicitly_requeued_and_can_restart(tmp_path: Path) -> None:
    control, store, clock = _control(tmp_path)
    created, _, _, running = _through_running(control)
    assert running.active_execution is not None
    clock.advance(59)
    with pytest.raises(AtlasCommandError, match="has not expired"):
        control.recover_expired_job(
            created.job_id,
            command_id="command:recover:bad",
            expected_revision=running.revision,
            actor=SYSTEM,
        )
    clock.advance(1)
    recovered = control.recover_expired_job(
        created.job_id,
        command_id="command:recover:001",
        expected_revision=running.revision,
        actor=SYSTEM,
    )
    assert recovered.status is JobStatus.QUEUED
    assert recovered.active_execution is None
    assert recovered.last_execution_id == running.active_execution.execution_id
    restarted = control.start_job(
        created.job_id,
        command_id="command:start:002",
        expected_revision=recovered.revision,
        actor=PATCHFORGE,
    )
    assert restarted.status is JobStatus.RUNNING
    assert restarted.active_execution is not None
    assert restarted.active_execution.execution_id != recovered.last_execution_id
    assert [event.event_type for event in store.list_audit_events(created.job_id)][-2:] == [
        AuditEventType.JOB_RECOVERED,
        AuditEventType.JOB_STARTED,
    ]


def test_runtime_failure_is_typed_terminal_state(tmp_path: Path) -> None:
    control, store, _ = _control(tmp_path)
    created, _, _, running = _through_running(control)
    failed = control.fail_job(
        created.job_id,
        FailureClassification.RUNTIME_ERROR,
        "Registered runtime returned a bounded execution failure.",
        retryable=True,
        command_id="command:fail:001",
        expected_revision=running.revision,
        actor=PATCHFORGE,
    )
    assert failed.status is JobStatus.FAILED
    assert failed.failure is not None
    assert failed.failure.classification is FailureClassification.RUNTIME_ERROR
    assert failed.active_execution is None
    assert store.list_audit_events(created.job_id)[-1].event_type is AuditEventType.JOB_FAILED
