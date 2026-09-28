"""Deterministic Atlas control-plane commands over the durable job store."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import TypeAdapter

from nexus.atlas.models import (
    ActorIdentity,
    ActorType,
    ApprovalAuditData,
    ApprovalDecision,
    ApprovalRecord,
    ApprovalState,
    AuditData,
    AuditEvent,
    AuditEventType,
    Capability,
    CommandId,
    CommandRecord,
    CreatedAuditData,
    ExecutionLease,
    FailureAuditData,
    FailureClassification,
    Job,
    JobFailure,
    JobSpec,
    JobStatus,
    PatchResult,
    RecoveryAuditData,
    ResultAuditData,
    ReviewAuditData,
    ReviewResult,
    ReviewVerdict,
    StructuredResult,
    TransitionAuditData,
)
from nexus.atlas.policy import AgentRegistry, AtlasPolicyError
from nexus.atlas.state_machine import require_transition
from nexus.atlas.storage import ConcurrencyError, SQLiteAtlasStore, canonical_sha256

Clock = Callable[[], datetime]
IdFactory = Callable[[], UUID]
_COMMAND_ADAPTER = TypeAdapter(CommandId)


class AtlasCommandError(ValueError):
    """A typed Atlas command is invalid for the current durable job."""


class AtlasControlPlane:
    """Synchronous command boundary; scheduling and agent execution are intentionally absent."""

    def __init__(
        self,
        store: SQLiteAtlasStore,
        registry: AgentRegistry,
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4

    def create_job(
        self,
        spec: JobSpec,
        *,
        command_id: str,
        actor: ActorIdentity,
    ) -> Job:
        command_id = self._command_id(command_id)
        self.registry.require_operator(actor)
        operation = "job.create"
        request_hash = self._request_hash(
            operation,
            actor,
            {"spec": spec.model_dump(mode="json")},
        )
        prior = self.store.lookup_command(command_id, operation, request_hash)
        if prior is not None:
            return prior
        now = self._now()
        job = Job(
            job_id=self._id_factory(),
            create_idempotency_key=command_id,
            agent_id=spec.agent_id,
            reviewer_agent_id=spec.reviewer_agent_id,
            request=spec.request,
            source=spec.source,
            budget=spec.budget,
            capabilities=spec.capabilities,
            created_at=now,
            updated_at=now,
        )
        event = self._event(
            before=None,
            after=job,
            event_type=AuditEventType.JOB_CREATED,
            actor=actor,
            command_id=command_id,
            data=CreatedAuditData(
                request_sha256=canonical_sha256(spec),
                create_idempotency_key=command_id,
            ),
        )
        command = self._command(command_id, operation, request_hash, job, now)
        return self.store.create(job, event, command)

    def validate_job(
        self,
        job_id: UUID,
        *,
        command_id: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        operation = "job.validate"
        fingerprint, prior, job = self._prepare(
            operation, job_id, command_id, expected_revision, actor
        )
        if prior is not None:
            return prior
        self.registry.require_operator(actor)
        self.registry.validate_job(self._spec(job))
        return self._transition(
            job,
            JobStatus.VALIDATED,
            AuditEventType.JOB_VALIDATED,
            TransitionAuditData(
                reason="Agent, reviewer, source, capabilities, and budget validated."
            ),
            operation,
            command_id,
            fingerprint,
            expected_revision,
            actor,
        )

    def queue_job(
        self,
        job_id: UUID,
        *,
        command_id: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        operation = "job.queue"
        fingerprint, prior, job = self._prepare(
            operation, job_id, command_id, expected_revision, actor
        )
        if prior is not None:
            return prior
        self.registry.require_operator(actor)
        return self._transition(
            job,
            JobStatus.QUEUED,
            AuditEventType.JOB_QUEUED,
            TransitionAuditData(reason="Validated job explicitly queued."),
            operation,
            command_id,
            fingerprint,
            expected_revision,
            actor,
        )

    def start_job(
        self,
        job_id: UUID,
        *,
        command_id: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        operation = "job.start"
        fingerprint, prior, job = self._prepare(
            operation, job_id, command_id, expected_revision, actor
        )
        if prior is not None:
            return prior
        self.registry.require_assigned_agent(job, actor)
        require_transition(job.status, JobStatus.RUNNING)
        now = self._now(job)
        lease = ExecutionLease(
            execution_id=self._id_factory(),
            claimed_by=actor.actor_id,
            started_at=now,
            expires_at=now + timedelta(seconds=job.budget.max_duration_seconds),
        )
        updated = self._updated(
            job,
            now,
            status=JobStatus.RUNNING,
            active_execution=lease,
            started_at=job.started_at or now,
        )
        return self._persist(
            job,
            updated,
            AuditEventType.JOB_STARTED,
            TransitionAuditData(reason="Assigned agent claimed a bounded execution lease."),
            operation,
            command_id,
            fingerprint,
            expected_revision,
            actor,
        )

    def submit_result(
        self,
        job_id: UUID,
        result: StructuredResult,
        *,
        command_id: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        operation = "job.submit_result"
        payload = {"result": result.model_dump(mode="json")}
        fingerprint, prior, job = self._prepare(
            operation, job_id, command_id, expected_revision, actor, payload
        )
        if prior is not None:
            return prior
        self.registry.require_assigned_agent(job, actor)
        require_transition(job.status, JobStatus.AWAITING_REVIEW)
        if job.active_execution is None:
            raise AtlasCommandError("Running job has no active execution lease")
        if isinstance(result, PatchResult):
            if Capability.PATCH_CREATE not in job.capabilities:
                raise AtlasPolicyError("Patch result requires patch.create capability")
            if result.base_sha != job.source.commit_sha:
                raise AtlasCommandError("Patch result base SHA does not match the source revision")
        now = self._now(job)
        execution_id = job.active_execution.execution_id
        updated = self._updated(
            job,
            now,
            status=JobStatus.AWAITING_REVIEW,
            approval_state=ApprovalState.PENDING,
            result=result,
            active_execution=None,
            last_execution_id=execution_id,
            review_requested_at=now,
        )
        return self._persist(
            job,
            updated,
            AuditEventType.RESULT_RECORDED,
            ResultAuditData(result_sha256=canonical_sha256(result), execution_id=execution_id),
            operation,
            command_id,
            fingerprint,
            expected_revision,
            actor,
        )

    def record_review(
        self,
        job_id: UUID,
        review: ReviewResult,
        *,
        command_id: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        operation = "job.record_review"
        payload = {"review": review.model_dump(mode="json")}
        fingerprint, prior, job = self._prepare(
            operation, job_id, command_id, expected_revision, actor, payload
        )
        if prior is not None:
            return prior
        self.registry.require_reviewer(job, actor)
        if job.status is not JobStatus.AWAITING_REVIEW:
            raise AtlasCommandError("Review can be recorded only while awaiting review")
        if job.review is not None:
            raise AtlasCommandError("Atlas v1 permits one immutable review per job")
        if review.reviewer_agent_id != actor.actor_id:
            raise AtlasPolicyError("Review identity does not match the reviewer actor")
        now = self._now(job)
        if review.created_at > now or (
            job.review_requested_at is not None and review.created_at < job.review_requested_at
        ):
            raise AtlasCommandError("Review timestamp is outside the review window")
        updated = self._updated(job, now, review=review)
        return self._persist(
            job,
            updated,
            AuditEventType.REVIEW_RECORDED,
            ReviewAuditData(review_id=review.review_id, verdict=review.verdict),
            operation,
            command_id,
            fingerprint,
            expected_revision,
            actor,
        )

    def approve_job(
        self,
        job_id: UUID,
        reason: str,
        *,
        command_id: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        return self._decide(
            job_id,
            ApprovalDecision.APPROVED,
            reason,
            command_id,
            expected_revision,
            actor,
        )

    def reject_job(
        self,
        job_id: UUID,
        reason: str,
        *,
        command_id: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        return self._decide(
            job_id,
            ApprovalDecision.REJECTED,
            reason,
            command_id,
            expected_revision,
            actor,
        )

    def complete_job(
        self,
        job_id: UUID,
        *,
        command_id: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        operation = "job.complete"
        fingerprint, prior, job = self._prepare(
            operation, job_id, command_id, expected_revision, actor
        )
        if prior is not None:
            return prior
        self.registry.require_system(actor)
        now = self._now(job)
        require_transition(job.status, JobStatus.COMPLETED)
        updated = self._updated(job, now, status=JobStatus.COMPLETED, terminal_at=now)
        return self._persist(
            job,
            updated,
            AuditEventType.JOB_COMPLETED,
            TransitionAuditData(reason="Approved reviewed result marked complete."),
            operation,
            command_id,
            fingerprint,
            expected_revision,
            actor,
        )

    def fail_job(
        self,
        job_id: UUID,
        classification: FailureClassification,
        message: str,
        *,
        retryable: bool,
        command_id: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        operation = "job.fail"
        payload = {
            "classification": classification.value,
            "message": message,
            "retryable": retryable,
        }
        fingerprint, prior, job = self._prepare(
            operation, job_id, command_id, expected_revision, actor, payload
        )
        if prior is not None:
            return prior
        if actor.actor_type is ActorType.AGENT:
            self.registry.require_assigned_agent(job, actor)
        else:
            self.registry.require_system(actor)
        require_transition(job.status, JobStatus.FAILED)
        now = self._now(job)
        last_execution_id = (
            job.active_execution.execution_id
            if job.active_execution is not None
            else job.last_execution_id
        )
        failure = JobFailure(
            classification=classification,
            message=message,
            retryable=retryable,
            failed_by=actor,
        )
        updated = self._updated(
            job,
            now,
            status=JobStatus.FAILED,
            failure=failure,
            active_execution=None,
            last_execution_id=last_execution_id,
            terminal_at=now,
        )
        return self._persist(
            job,
            updated,
            AuditEventType.JOB_FAILED,
            FailureAuditData(classification=classification),
            operation,
            command_id,
            fingerprint,
            expected_revision,
            actor,
        )

    def recover_expired_job(
        self,
        job_id: UUID,
        *,
        command_id: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        operation = "job.recover_expired"
        fingerprint, prior, job = self._prepare(
            operation, job_id, command_id, expected_revision, actor
        )
        if prior is not None:
            return prior
        self.registry.require_system(actor)
        require_transition(job.status, JobStatus.QUEUED)
        if job.active_execution is None:
            raise AtlasCommandError("Running job has no active execution lease")
        now = self._now(job)
        if now < job.active_execution.expires_at:
            raise AtlasCommandError("Execution lease has not expired")
        execution_id = job.active_execution.execution_id
        updated = self._updated(
            job,
            now,
            status=JobStatus.QUEUED,
            active_execution=None,
            last_execution_id=execution_id,
        )
        return self._persist(
            job,
            updated,
            AuditEventType.JOB_RECOVERED,
            RecoveryAuditData(expired_execution_id=execution_id),
            operation,
            command_id,
            fingerprint,
            expected_revision,
            actor,
        )

    def _decide(
        self,
        job_id: UUID,
        decision: ApprovalDecision,
        reason: str,
        command_id: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        operation = f"job.{decision.value}"
        payload = {"reason": reason}
        fingerprint, prior, job = self._prepare(
            operation, job_id, command_id, expected_revision, actor, payload
        )
        if prior is not None:
            return prior
        self.registry.require_human(actor)
        if job.review is None:
            raise AtlasCommandError("Human decision requires a recorded reviewer result")
        target = JobStatus.APPROVED if decision is ApprovalDecision.APPROVED else JobStatus.REJECTED
        if decision is ApprovalDecision.APPROVED and job.review.verdict is not ReviewVerdict.PASSED:
            raise AtlasCommandError("Human approval requires a passed reviewer result")
        require_transition(job.status, target)
        now = self._now(job)
        approval = ApprovalRecord(
            approval_id=self._id_factory(),
            decision=decision,
            decided_by=actor,
            reason=reason,
            decided_at=now,
        )
        updated = self._updated(
            job,
            now,
            status=target,
            approval_state=(
                ApprovalState.APPROVED
                if decision is ApprovalDecision.APPROVED
                else ApprovalState.REJECTED
            ),
            approval=approval,
        )
        event_type = (
            AuditEventType.JOB_APPROVED
            if decision is ApprovalDecision.APPROVED
            else AuditEventType.JOB_REJECTED
        )
        return self._persist(
            job,
            updated,
            event_type,
            ApprovalAuditData(approval_id=approval.approval_id, decision=decision),
            operation,
            command_id,
            fingerprint,
            expected_revision,
            actor,
        )

    def _transition(
        self,
        job: Job,
        target: JobStatus,
        event_type: AuditEventType,
        data: AuditData,
        operation: str,
        command_id: str,
        fingerprint: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        require_transition(job.status, target)
        now = self._now(job)
        updated = self._updated(job, now, status=target)
        return self._persist(
            job,
            updated,
            event_type,
            data,
            operation,
            command_id,
            fingerprint,
            expected_revision,
            actor,
        )

    def _prepare(
        self,
        operation: str,
        job_id: UUID,
        command_id: str,
        expected_revision: int,
        actor: ActorIdentity,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[str, Job | None, Job]:
        command_id = self._command_id(command_id)
        if expected_revision < 0:
            raise AtlasCommandError("Expected revision cannot be negative")
        request_hash = self._request_hash(
            operation,
            actor,
            {
                "job_id": str(job_id),
                "expected_revision": expected_revision,
                **(payload or {}),
            },
        )
        prior = self.store.lookup_command(command_id, operation, request_hash)
        if prior is not None:
            return request_hash, prior, prior
        job = self.store.get_job(job_id)
        if job.revision != expected_revision:
            raise ConcurrencyError(
                f"Atlas expected revision {expected_revision}, found {job.revision}"
            )
        return request_hash, None, job

    def _persist(
        self,
        before: Job,
        after: Job,
        event_type: AuditEventType,
        data: AuditData,
        operation: str,
        command_id: str,
        fingerprint: str,
        expected_revision: int,
        actor: ActorIdentity,
    ) -> Job:
        event = self._event(
            before=before,
            after=after,
            event_type=event_type,
            actor=actor,
            command_id=command_id,
            data=data,
        )
        command = self._command(command_id, operation, fingerprint, after, after.updated_at)
        return self.store.update(expected_revision, after, event, command)

    @staticmethod
    def _updated(job: Job, now: datetime, **changes: object) -> Job:
        values = job.model_dump(mode="python")
        values.update(changes)
        values["updated_at"] = now
        values["revision"] = job.revision + 1
        return Job.model_validate(values)

    @staticmethod
    def _spec(job: Job) -> JobSpec:
        return JobSpec(
            agent_id=job.agent_id,
            reviewer_agent_id=job.reviewer_agent_id,
            request=job.request,
            source=job.source,
            budget=job.budget,
            capabilities=job.capabilities,
        )

    def _event(
        self,
        *,
        before: Job | None,
        after: Job,
        event_type: AuditEventType,
        actor: ActorIdentity,
        command_id: str,
        data: AuditData,
    ) -> AuditEvent:
        sequence = after.revision + 1
        event_id = uuid5(
            NAMESPACE_URL,
            f"nexus.atlas:{after.job_id}:{sequence}:{command_id}",
        )
        return AuditEvent(
            event_id=event_id,
            job_id=after.job_id,
            sequence=sequence,
            job_revision=after.revision,
            event_type=event_type,
            actor=actor,
            command_id=command_id,
            occurred_at=after.updated_at,
            from_status=(before.status if before is not None else None),
            to_status=after.status,
            data=data,
        )

    @staticmethod
    def _command(
        command_id: str,
        operation: str,
        request_sha256: str,
        job: Job,
        created_at: datetime,
    ) -> CommandRecord:
        return CommandRecord(
            command_id=command_id,
            operation=operation,
            request_sha256=request_sha256,
            job_id=job.job_id,
            response_revision=job.revision,
            created_at=created_at,
        )

    @staticmethod
    def _request_hash(
        operation: str,
        actor: ActorIdentity,
        payload: Mapping[str, Any],
    ) -> str:
        return canonical_sha256(
            {
                "operation": operation,
                "actor": actor.model_dump(mode="json"),
                "payload": dict(payload),
            }
        )

    @staticmethod
    def _command_id(value: str) -> str:
        return _COMMAND_ADAPTER.validate_python(value)

    def _now(self, job: Job | None = None) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise AtlasCommandError("Atlas clock must return a timezone-aware datetime")
        if job is not None and value < job.updated_at:
            raise AtlasCommandError("Atlas clock moved behind durable job state")
        return value
