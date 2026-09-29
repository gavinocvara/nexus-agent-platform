"""PatchForge -> SentinelQA -> Atlas: only a passed independent review unlocks approval."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from nexus.atlas.models import (
    ActorIdentity,
    ActorType,
    AgentPolicy,
    ApprovalState,
    Capability,
    ExecutionBudget,
    JobSpec,
    JobStatus,
    ReviewVerdict,
    SourceRevision,
    TaskRequest,
)
from nexus.atlas.policy import AgentRegistry
from nexus.atlas.service import AtlasCommandError, AtlasControlPlane
from nexus.atlas.storage import SQLiteAtlasStore
from nexus.patchforge.attestor import LocalArtifactStore
from nexus.patchforge.canonical import canonical_json
from nexus.patchforge.sandbox import FakeSandbox
from nexus.sentinelqa.catalog import fix_and_weaken_test, honest_fix
from nexus.sentinelqa.harness import SentinelQAHarness, SentinelScenario
from nexus.sentinelqa.models import SENTINELQA_AGENT_ID, VERDICT_ARTIFACT_TYPE

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
SYSTEM = ActorIdentity(actor_type=ActorType.SYSTEM, actor_id="atlas.system")
HUMAN = ActorIdentity(actor_type=ActorType.HUMAN, actor_id="owner.reviewer")
PATCHFORGE = ActorIdentity(actor_type=ActorType.AGENT, actor_id="patchforge.engineer")
SENTINEL = ActorIdentity(actor_type=ActorType.AGENT, actor_id=SENTINELQA_AGENT_ID)


class Clock:
    def __init__(self) -> None:
        self.value = NOW - timedelta(minutes=5)

    def __call__(self) -> datetime:
        self.value += timedelta(seconds=1)
        return self.value


def _registry(repository: str) -> AgentRegistry:
    budget = ExecutionBudget(
        max_duration_seconds=600,
        max_tool_calls=100,
        max_input_tokens=100_000,
        max_output_tokens=10_000,
        max_artifact_bytes=5_000_000,
    )
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
                max_budget=budget,
                repository_prefixes=[repository],
            ),
            AgentPolicy(
                agent_id=SENTINEL.actor_id,
                runtime_kind="sentinelqa",
                runtime_version="lite-v1",
                capabilities=[
                    Capability.SOURCE_READ,
                    Capability.TEST_EXECUTE,
                    Capability.REVIEW_READ,
                    Capability.REVIEW_SUBMIT,
                ],
                max_budget=budget,
                repository_prefixes=[repository],
            ),
        ]
    )


def _drive(tmp_path: Path, scenario: SentinelScenario) -> tuple[SQLiteAtlasStore, UUID, bool]:
    """Run PatchForge and SentinelQA, then push both results through Atlas."""

    sentinel = SentinelQAHarness(tmp_path / "harness", now=NOW).run(scenario)
    task = sentinel.candidate.task
    repository = str(task.source.repository_url)
    clock = Clock()
    ids = iter(UUID(int=index) for index in range(1, 1000))
    store = SQLiteAtlasStore(tmp_path / "atlas.sqlite3")
    control = AtlasControlPlane(
        store,
        _registry(repository),
        clock=clock,
        id_factory=lambda: next(ids),
    )
    spec = JobSpec(
        agent_id=PATCHFORGE.actor_id,
        reviewer_agent_id=SENTINEL.actor_id,
        request=TaskRequest(
            task_type="software_change",
            title=task.title,
            instructions=task.instructions,
            acceptance_criteria=list(task.acceptance_criteria),
        ),
        source=SourceRevision(
            repository_url=task.source.repository_url, commit_sha=task.source.commit_sha
        ),
        budget=ExecutionBudget(
            max_duration_seconds=600,
            max_tool_calls=100,
            max_input_tokens=100_000,
            max_output_tokens=10_000,
            max_artifact_bytes=5_000_000,
        ),
        capabilities=[
            Capability.SOURCE_READ,
            Capability.WORKTREE_WRITE,
            Capability.TEST_EXECUTE,
            Capability.PATCH_CREATE,
        ],
    )
    job = control.create_job(spec, command_id="command:create:001", actor=SYSTEM)
    job = control.validate_job(
        job.job_id, command_id="command:validate:001", expected_revision=job.revision, actor=SYSTEM
    )
    job = control.queue_job(
        job.job_id, command_id="command:queue:001", expected_revision=job.revision, actor=SYSTEM
    )
    job = control.start_job(
        job.job_id, command_id="command:start:001", expected_revision=job.revision, actor=PATCHFORGE
    )
    job = control.submit_result(
        job.job_id,
        sentinel.candidate.result.to_atlas_result(),
        command_id="command:result:001",
        expected_revision=job.revision,
        actor=PATCHFORGE,
    )
    assert job.status is JobStatus.AWAITING_REVIEW
    verdict = sentinel.verdict
    # The verdict is stored content-addressed and cited as the review's evidence.
    verdicts = LocalArtifactStore(tmp_path / "verdicts")
    evidence = verdicts.put(
        canonical_json(verdict).encode("utf-8"), artifact_type=VERDICT_ARTIFACT_TYPE
    )
    review = verdict.to_atlas_review(evidence).model_copy(update={"created_at": clock()})
    job = control.record_review(
        job.job_id,
        review,
        command_id="command:review:001",
        expected_revision=job.revision,
        actor=SENTINEL,
    )
    assert job.review is not None and job.review.evidence[0].sha256 == verdict.verdict_sha256
    try:
        approved = control.approve_job(
            job.job_id,
            "Independent verification passed.",
            command_id="command:approve:001",
            expected_revision=job.revision,
            actor=HUMAN,
        )
    except AtlasCommandError:
        return store, job.job_id, False
    assert approved.approval_state is ApprovalState.APPROVED
    return store, job.job_id, True


def test_passed_sentinelqa_review_unlocks_human_approval(tmp_path: Path) -> None:
    store, job_id, approvable = _drive(tmp_path, honest_fix())
    assert approvable is True
    job = store.get_job(job_id)
    assert job.review is not None and job.review.verdict is ReviewVerdict.PASSED


def test_failed_sentinelqa_review_blocks_approval(tmp_path: Path) -> None:
    store, job_id, approvable = _drive(tmp_path, fix_and_weaken_test())
    assert approvable is False
    job = store.get_job(job_id)
    assert job.review is not None and job.review.verdict is ReviewVerdict.FAILED
    assert job.status is JobStatus.AWAITING_REVIEW
    assert "specification_modified" in job.review.summary


def test_sentinelqa_cannot_approve_its_own_review(tmp_path: Path) -> None:
    sentinel = SentinelQAHarness(tmp_path / "harness", now=NOW).run(honest_fix())
    assert sentinel.verdict.verdict is ReviewVerdict.PASSED
    # Atlas approvals require a human actor; the agent identity is refused at the contract.
    from nexus.atlas.models import ApprovalDecision, ApprovalRecord

    with pytest.raises(ValueError, match="human actor"):
        ApprovalRecord(
            approval_id=UUID(int=1),
            decision=ApprovalDecision.APPROVED,
            decided_by=SENTINEL,
            reason="self-approval",
            decided_at=NOW,
        )
    assert isinstance(FakeSandbox([]), FakeSandbox)
