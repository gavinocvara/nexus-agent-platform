"""Atlas capability, source, budget, and actor policy tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from nexus.atlas.models import (
    ActorIdentity,
    ActorType,
    AgentPolicy,
    Capability,
    ExecutionBudget,
    Job,
    JobSpec,
    SourceRevision,
    TaskRequest,
)
from nexus.atlas.policy import AgentRegistry, AtlasPolicyError


def _budget(multiplier: int = 1) -> ExecutionBudget:
    return ExecutionBudget(
        max_duration_seconds=600 * multiplier,
        max_tool_calls=40 * multiplier,
        max_input_tokens=100_000 * multiplier,
        max_output_tokens=20_000 * multiplier,
        max_artifact_bytes=10_000_000 * multiplier,
    )


def _registry(*, reviewer_capability: bool = True) -> AgentRegistry:
    return AgentRegistry(
        [
            AgentPolicy(
                agent_id="patchforge.engineer",
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
                agent_id="sentinelqa.reviewer",
                runtime_kind="sentinelqa",
                runtime_version="v1",
                capabilities=(
                    [Capability.REVIEW_READ, Capability.REVIEW_SUBMIT]
                    if reviewer_capability
                    else [Capability.REVIEW_READ]
                ),
                max_budget=_budget(),
                repository_prefixes=["https://github.com/gavinocvara/"],
            ),
        ]
    )


def _spec(**updates: object) -> JobSpec:
    values = {
        "agent_id": "patchforge.engineer",
        "reviewer_agent_id": "sentinelqa.reviewer",
        "request": TaskRequest(
            task_type="software_change",
            title="Patch an issue",
            instructions="Implement the bounded change.",
        ),
        "source": SourceRevision(
            repository_url="https://github.com/gavinocvara/nexus-agent-platform",
            commit_sha="1" * 40,
        ),
        "budget": _budget(),
        "capabilities": [Capability.SOURCE_READ, Capability.PATCH_CREATE],
    }
    values.update(updates)
    return JobSpec.model_validate(values)


def test_registry_accepts_explicit_capabilities_budget_source_and_reviewer() -> None:
    _registry().validate_job(_spec())


def test_registry_rejects_unknown_agent() -> None:
    with pytest.raises(AtlasPolicyError, match="not registered"):
        _registry().validate_job(_spec(agent_id="unknown.agent"))


def test_registry_rejects_missing_capability() -> None:
    with pytest.raises(AtlasPolicyError, match="review.submit"):
        _registry().validate_job(
            _spec(
                capabilities=[
                    Capability.SOURCE_READ,
                    Capability.PATCH_CREATE,
                    Capability.REVIEW_SUBMIT,
                ]
            )
        )


def test_registry_rejects_excess_budget() -> None:
    with pytest.raises(AtlasPolicyError, match="max_duration_seconds"):
        _registry().validate_job(_spec(budget=_budget(2)))


def test_registry_rejects_repository_outside_scope() -> None:
    with pytest.raises(AtlasPolicyError, match="outside"):
        _registry().validate_job(
            _spec(
                source=SourceRevision(
                    repository_url="https://github.com/other/project",
                    commit_sha="1" * 40,
                )
            )
        )


def test_registry_repository_prefix_uses_a_path_boundary() -> None:
    registry = AgentRegistry(
        [
            AgentPolicy(
                agent_id="patchforge.engineer",
                runtime_kind="patchforge",
                runtime_version="v1",
                capabilities=[Capability.SOURCE_READ, Capability.PATCH_CREATE],
                max_budget=_budget(),
                repository_prefixes=["https://github.com/gavinocvara/nexus"],
            ),
            _registry().get("sentinelqa.reviewer"),
        ]
    )
    with pytest.raises(AtlasPolicyError, match="outside"):
        registry.validate_job(
            _spec(
                source=SourceRevision(
                    repository_url="https://github.com/gavinocvara/nexus-evil",
                    commit_sha="1" * 40,
                )
            )
        )


def test_registry_rejects_reviewer_without_submit_capability() -> None:
    with pytest.raises(AtlasPolicyError, match="review.submit"):
        _registry(reviewer_capability=False).validate_job(_spec())


def test_actor_checks_do_not_grant_ambient_agent_access() -> None:
    spec = _spec()
    job = Job(
        job_id=UUID(int=1),
        create_idempotency_key="create:12345678",
        **spec.model_dump(mode="python"),
        created_at=datetime(2026, 9, 27, 12, tzinfo=UTC),
        updated_at=datetime(2026, 9, 27, 12, tzinfo=UTC),
    )
    with pytest.raises(AtlasPolicyError, match="assigned agent"):
        _registry().require_assigned_agent(
            job,
            ActorIdentity(actor_type=ActorType.AGENT, actor_id="sentinelqa.reviewer"),
        )
