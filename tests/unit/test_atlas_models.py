"""Strict Atlas model and future PatchForge contract tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from nexus.atlas.models import (
    ActorIdentity,
    ActorType,
    ApprovalDecision,
    ApprovalRecord,
    ArtifactReference,
    Capability,
    CheckResult,
    ExecutionBudget,
    JobSpec,
    PatchResult,
    SourceRevision,
    TaskRequest,
)

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def _budget() -> ExecutionBudget:
    return ExecutionBudget(
        max_duration_seconds=600,
        max_tool_calls=40,
        max_input_tokens=100_000,
        max_output_tokens=20_000,
        max_artifact_bytes=10_000_000,
    )


def _artifact() -> ArtifactReference:
    return ArtifactReference(
        artifact_type="unified_diff",
        uri="atlas://artifacts/patch.diff",
        sha256="a" * 64,
    )


def test_models_are_strict_and_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TaskRequest.model_validate(
            {
                "task_type": "software_change",
                "title": "Patch an issue",
                "instructions": "Implement the bounded change.",
                "ambient_shell": True,
            }
        )

    with pytest.raises(ValidationError, match="int_type"):
        ExecutionBudget.model_validate(
            {
                "max_duration_seconds": "600",
                "max_tool_calls": 40,
                "max_input_tokens": 100_000,
                "max_output_tokens": 20_000,
                "max_artifact_bytes": 10_000_000,
            }
        )


def test_job_spec_canonicalizes_unique_capabilities() -> None:
    spec = JobSpec(
        agent_id="patchforge.engineer",
        reviewer_agent_id="sentinelqa.reviewer",
        request=TaskRequest(
            task_type="software_change",
            title="Patch an issue",
            instructions="Implement the bounded change.",
        ),
        source=SourceRevision(
            repository_url="https://github.com/gavinocvara/nexus-agent-platform",
            commit_sha="1" * 40,
        ),
        budget=_budget(),
        capabilities=[Capability.PATCH_CREATE, Capability.SOURCE_READ],
    )
    assert spec.capabilities == [Capability.PATCH_CREATE, Capability.SOURCE_READ]
    invalid = spec.model_dump(mode="python")
    invalid["capabilities"] = [Capability.SOURCE_READ, Capability.SOURCE_READ]
    with pytest.raises(ValidationError, match="unique"):
        JobSpec.model_validate(invalid)


def test_patch_result_is_typed_and_rejects_unsafe_paths() -> None:
    result = PatchResult(
        summary="Produced a bounded tested patch.",
        base_sha="1" * 40,
        proposed_head_sha="2" * 40,
        patch_artifact=_artifact(),
        changed_files=["src/nexus/example.py"],
        checks=[CheckResult(name="pytest", passed=True, summary="All tests passed.")],
    )
    assert result.result_type == "patch"
    invalid = result.model_dump(mode="python")
    invalid["changed_files"] = ["../secret.txt"]
    with pytest.raises(ValidationError, match="repository-relative"):
        PatchResult.model_validate(invalid)


def test_approval_record_requires_a_human_actor() -> None:
    with pytest.raises(ValidationError, match="human actor"):
        ApprovalRecord(
            approval_id=UUID(int=1),
            decision=ApprovalDecision.APPROVED,
            decided_by=ActorIdentity(actor_type=ActorType.AGENT, actor_id="patchforge.engineer"),
            reason="Reviewed.",
            decided_at=NOW,
        )


@pytest.mark.parametrize("commit_sha", ["abc", "g" * 40, "1" * 39, "1" * 41])
def test_source_revision_requires_a_full_hex_commit(commit_sha: str) -> None:
    with pytest.raises(ValidationError):
        SourceRevision(
            repository_url="https://github.com/gavinocvara/nexus-agent-platform",
            commit_sha=commit_sha,
        )


@pytest.mark.parametrize(
    "repository_url",
    [
        "https://user:password@github.com/gavinocvara/nexus-agent-platform",
        "https://github.com/gavinocvara/nexus-agent-platform?token=secret",
        "https://github.com/gavinocvara/nexus-agent-platform#fragment",
    ],
)
def test_source_revision_rejects_url_credentials_and_suffixes(repository_url: str) -> None:
    with pytest.raises(ValidationError, match="cannot contain"):
        SourceRevision(repository_url=repository_url, commit_sha="1" * 40)
