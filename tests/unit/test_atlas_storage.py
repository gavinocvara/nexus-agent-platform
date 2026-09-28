"""Durability and append-only persistence tests for Atlas."""

import sqlite3
from pathlib import Path

import pytest

from nexus.atlas.models import (
    ActorIdentity,
    ActorType,
    AgentPolicy,
    Capability,
    ExecutionBudget,
    JobSpec,
    SourceRevision,
    TaskRequest,
)
from nexus.atlas.policy import AgentRegistry
from nexus.atlas.service import AtlasControlPlane
from nexus.atlas.storage import SQLiteAtlasStore

SYSTEM = ActorIdentity(actor_type=ActorType.SYSTEM, actor_id="atlas.system")


def _budget() -> ExecutionBudget:
    return ExecutionBudget(
        max_duration_seconds=60,
        max_tool_calls=20,
        max_input_tokens=50_000,
        max_output_tokens=10_000,
        max_artifact_bytes=5_000_000,
    )


def _spec() -> JobSpec:
    return JobSpec(
        agent_id="patchforge.engineer",
        reviewer_agent_id="sentinelqa.reviewer",
        request=TaskRequest(
            task_type="software_change",
            title="Implement a bounded change",
            instructions="Modify only the requested repository behavior.",
        ),
        source=SourceRevision(
            repository_url="https://github.com/gavinocvara/nexus-agent-platform",
            commit_sha="1" * 40,
        ),
        budget=_budget(),
        capabilities=[Capability.SOURCE_READ, Capability.PATCH_CREATE],
    )


def _control(tmp_path: Path) -> tuple[AtlasControlPlane, SQLiteAtlasStore]:
    store = SQLiteAtlasStore(tmp_path / "atlas.sqlite3")
    registry = AgentRegistry(
        [
            AgentPolicy(
                agent_id="patchforge.engineer",
                runtime_kind="patchforge",
                runtime_version="v1",
                capabilities=[Capability.SOURCE_READ, Capability.PATCH_CREATE],
                max_budget=_budget(),
                repository_prefixes=["https://github.com/gavinocvara/"],
            ),
            AgentPolicy(
                agent_id="sentinelqa.reviewer",
                runtime_kind="sentinelqa",
                runtime_version="v1",
                capabilities=[Capability.REVIEW_READ, Capability.REVIEW_SUBMIT],
                max_budget=_budget(),
                repository_prefixes=["https://github.com/gavinocvara/"],
            ),
        ]
    )
    return AtlasControlPlane(store, registry), store


def test_job_and_audit_survive_store_reconstruction(tmp_path: Path) -> None:
    control, store = _control(tmp_path)
    created = control.create_job(_spec(), command_id="command:create:001", actor=SYSTEM)
    control.validate_job(
        created.job_id,
        command_id="command:validate:001",
        expected_revision=0,
        actor=SYSTEM,
    )

    reopened = SQLiteAtlasStore(store.path)
    reopened.integrity_check()
    assert reopened.get_job(created.job_id).revision == 1
    assert len(reopened.list_audit_events(created.job_id)) == 2
    assert reopened.list_jobs() == [reopened.get_job(created.job_id)]


@pytest.mark.parametrize("statement", ["UPDATE", "DELETE"])
def test_database_guards_make_audit_rows_append_only(tmp_path: Path, statement: str) -> None:
    control, store = _control(tmp_path)
    created = control.create_job(_spec(), command_id="command:create:001", actor=SYSTEM)
    event = store.list_audit_events(created.job_id)[0]
    with (
        sqlite3.connect(store.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="append-only"),
    ):
        if statement == "UPDATE":
            connection.execute(
                "UPDATE atlas_audit_events SET event_type = ? WHERE event_id = ?",
                ("tampered", str(event.event_id)),
            )
        else:
            connection.execute(
                "DELETE FROM atlas_audit_events WHERE event_id = ?",
                (str(event.event_id),),
            )


@pytest.mark.parametrize("statement", ["UPDATE", "DELETE"])
def test_database_guards_make_command_rows_immutable(tmp_path: Path, statement: str) -> None:
    control, store = _control(tmp_path)
    control.create_job(_spec(), command_id="command:create:001", actor=SYSTEM)
    with (
        sqlite3.connect(store.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        if statement == "UPDATE":
            connection.execute(
                "UPDATE atlas_commands SET operation = ? WHERE command_id = ?",
                ("tampered", "command:create:001"),
            )
        else:
            connection.execute(
                "DELETE FROM atlas_commands WHERE command_id = ?",
                ("command:create:001",),
            )
