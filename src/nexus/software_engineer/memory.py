"""The resident engineer's private, provenance-backed memory.

Namespace ``software_engineer.resident`` is isolated: the store refuses any other agent or
namespace, and nothing here reads AegisOps memories. Every record says how it is known
(observation, inference, owner decision, validated fact, failed hypothesis), where it came
from, how confident the runtime is, and when it stopped being current. Guesses stay
inferences: a record becomes a validated fact only with validation evidence or an owner
decision behind it. Content that looks like an instruction or a secret is refused so the
memory cannot become an injection channel.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, StringConstraints, ValidationError, model_validator

from nexus.atlas.models import Sha256, StrictModel
from nexus.patchforge.canonical import canonical_json, canonical_sha256
from nexus.software_engineer.models import SOFTWARE_ENGINEER_AGENT_ID, Percent
from nexus.software_engineer.trust import contains_credential, detect_instruction_like_text

MEMORY_NAMESPACE: Literal["software_engineer.resident"] = "software_engineer.resident"
MEMORY_SCHEMA_VERSION = 1
_MEMORY_NAMESPACE_UUID = UUID("9e4f1c2d-6a7b-4c8d-9e0f-1a2b3c4d5e6f")
Tag = Annotated[
    str, StringConstraints(min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_.:-]*$")
]
Content = Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class MemoryCategory(StrEnum):
    REPOSITORY_KNOWLEDGE = "repository_knowledge"
    ENGINEERING_LESSON = "engineering_lesson"
    OWNER_PREFERENCE = "owner_preference"
    DECISION = "decision"
    INCIDENT = "incident"
    BACKLOG_ITEM = "backlog_item"
    SELF_EVALUATION = "self_evaluation"


class EpistemicStatus(StrEnum):
    OBSERVATION = "observation"
    INFERENCE = "inference"
    OWNER_DECISION = "owner_decision"
    VALIDATED_FACT = "validated_fact"
    FAILED_HYPOTHESIS = "failed_hypothesis"


_STATUS_WEIGHT = {
    EpistemicStatus.OWNER_DECISION: 4,
    EpistemicStatus.VALIDATED_FACT: 3,
    EpistemicStatus.FAILED_HYPOTHESIS: 2,
    EpistemicStatus.OBSERVATION: 1,
    EpistemicStatus.INFERENCE: 0,
}


class MemoryLifecycle(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"
    DISPUTED = "disputed"


class MemorySource(StrEnum):
    CYCLE_RECORD = "cycle_record"
    OWNER_DECISION = "owner_decision"
    VALIDATION_EVIDENCE = "validation_evidence"
    INSPECTION = "inspection"


class MemoryProvenance(StrictModel):
    source: MemorySource
    cycle_id: UUID
    evidence_sha256: list[Sha256] = Field(default_factory=list, max_length=20)
    owner_decision_id: UUID | None = None


class EngineerMemory(StrictModel):
    schema_version: Literal[1] = 1
    memory_id: UUID
    agent_id: Literal["software_engineer.resident"] = SOFTWARE_ENGINEER_AGENT_ID
    namespace: Literal["software_engineer.resident"] = MEMORY_NAMESPACE
    category: MemoryCategory
    status: EpistemicStatus
    content: Content
    tags: list[Tag] = Field(default_factory=list, max_length=20)
    confidence: Percent
    provenance: MemoryProvenance
    created_at: AwareDatetime
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    lifecycle: MemoryLifecycle = MemoryLifecycle.ACTIVE
    supersedes: list[UUID] = Field(default_factory=list, max_length=20)
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_memory(self) -> EngineerMemory:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("Memory validity interval is inverted")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("Memory tags must be unique")
        provenance = self.provenance
        if self.status is EpistemicStatus.VALIDATED_FACT and not (
            provenance.evidence_sha256 or provenance.owner_decision_id is not None
        ):
            raise ValueError("A validated fact needs validation evidence or an owner decision")
        if self.status is EpistemicStatus.OWNER_DECISION and provenance.owner_decision_id is None:
            raise ValueError("An owner-decision memory must cite the decision")
        if self.status is EpistemicStatus.INFERENCE and self.confidence > 80:
            raise ValueError("An inference cannot claim more than 80% confidence")
        serialized = canonical_json(self)
        if contains_credential(serialized):
            raise ValueError("Memory content contains a secret-shaped value")
        if detect_instruction_like_text(self.content):
            raise ValueError("Memory content looks like an instruction and is refused")
        return self

    @property
    def content_sha256(self) -> str:
        """Identity used for deduplication: what is claimed, how, and in which category."""

        return sha256(
            f"{self.category.value}\n{self.status.value}\n{self.content.strip().casefold()}".encode()
        ).hexdigest()

    @property
    def is_trustworthy(self) -> bool:
        return self.status in {EpistemicStatus.VALIDATED_FACT, EpistemicStatus.OWNER_DECISION}


class MemoryQuery(StrictModel):
    category: MemoryCategory | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=20)
    statuses: list[EpistemicStatus] = Field(default_factory=list, max_length=5)
    as_of: AwareDatetime | None = None
    limit: int = Field(default=20, ge=1, le=200)


_SELECT_ONE = "SELECT record_json FROM engineer_memories WHERE namespace = ? AND memory_id = ?"


class MemoryStoreError(RuntimeError):
    """The private store is unavailable, corrupt, or was asked to cross its boundary."""


def memory_id_for(cycle_id: UUID, sequence: int) -> UUID:
    return uuid5(_MEMORY_NAMESPACE_UUID, f"{cycle_id}:{sequence}")


class EngineerMemoryStore:
    """Durable SQLite store scoped to the resident engineer's namespace."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS engineer_memories (
                        memory_id TEXT PRIMARY KEY,
                        agent_id TEXT NOT NULL,
                        namespace TEXT NOT NULL,
                        category TEXT NOT NULL,
                        status TEXT NOT NULL,
                        lifecycle TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        record_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_engineer_memories_lookup
                    ON engineer_memories(namespace, lifecycle, category, created_at, memory_id)
                    """
                )
                connection.commit()
        except (OSError, sqlite3.Error) as exc:
            raise MemoryStoreError("Engineer memory initialization failed") from exc

    def remember(self, memory: EngineerMemory) -> tuple[UUID, bool]:
        """Insert unless an active record makes the same claim; return ``(id, written)``."""

        self._guard(memory)
        self.initialize()
        try:
            with closing(self._connect()) as connection:
                existing = connection.execute(
                    """
                    SELECT memory_id FROM engineer_memories
                    WHERE namespace = ? AND lifecycle = ? AND content_sha256 = ?
                    ORDER BY created_at DESC, memory_id ASC LIMIT 1
                    """,
                    (MEMORY_NAMESPACE, MemoryLifecycle.ACTIVE.value, memory.content_sha256),
                ).fetchone()
                if existing is not None:
                    return UUID(existing[0]), False
                self._insert(connection, memory)
                connection.commit()
        except sqlite3.Error as exc:
            raise MemoryStoreError("Engineer memory write failed") from exc
        return memory.memory_id, True

    def correct(self, memory_id: UUID, replacement: EngineerMemory) -> UUID:
        """Supersede one record with a corrected version; history is kept, never edited."""

        self._guard(replacement)
        self.initialize()
        if memory_id not in replacement.supersedes:
            replacement = replacement.model_copy(
                update={"supersedes": [*replacement.supersedes, memory_id]}
            )
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    _SELECT_ONE,
                    (MEMORY_NAMESPACE, str(memory_id)),
                ).fetchone()
                if row is None:
                    raise MemoryStoreError("Cannot correct an unknown memory")
                previous = EngineerMemory.model_validate_json(row[0])
                replacement = replacement.model_copy(update={"version": previous.version + 1})
                self._set_lifecycle(connection, previous, MemoryLifecycle.SUPERSEDED)
                self._insert(connection, replacement)
                connection.commit()
        except (sqlite3.Error, ValidationError) as exc:
            raise MemoryStoreError("Engineer memory correction failed") from exc
        return replacement.memory_id

    def invalidate(self, memory_id: UUID, *, disputed: bool = False) -> None:
        self.initialize()
        target = MemoryLifecycle.DISPUTED if disputed else MemoryLifecycle.INVALIDATED
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    _SELECT_ONE,
                    (MEMORY_NAMESPACE, str(memory_id)),
                ).fetchone()
                if row is None:
                    raise MemoryStoreError("Cannot invalidate an unknown memory")
                self._set_lifecycle(connection, EngineerMemory.model_validate_json(row[0]), target)
                connection.commit()
        except (sqlite3.Error, ValidationError) as exc:
            raise MemoryStoreError("Engineer memory invalidation failed") from exc

    def retrieve(self, query: MemoryQuery) -> list[EngineerMemory]:
        """Active, currently valid records; owner decisions and validated facts first."""

        records = [
            item
            for item in self.load_all()
            if item.lifecycle is MemoryLifecycle.ACTIVE
            and (query.category is None or item.category is query.category)
            and (not query.statuses or item.status in query.statuses)
            and set(query.tags).issubset(item.tags)
            and (
                query.as_of is None
                or (
                    item.valid_from <= query.as_of
                    and (item.valid_to is None or query.as_of < item.valid_to)
                )
            )
        ]
        records.sort(
            key=lambda item: (
                -_STATUS_WEIGHT[item.status],
                -item.confidence,
                -item.created_at.timestamp(),
                str(item.memory_id),
            )
        )
        return records[: query.limit]

    def load_all(self) -> list[EngineerMemory]:
        self.initialize()
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT memory_id, agent_id, namespace, category, status, lifecycle,
                           content_sha256, record_json
                    FROM engineer_memories WHERE namespace = ? ORDER BY memory_id ASC
                    """,
                    (MEMORY_NAMESPACE,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryStoreError("Engineer memory read failed") from exc
        records: list[EngineerMemory] = []
        try:
            for row in rows:
                record = EngineerMemory.model_validate_json(row[7])
                expected = (
                    str(record.memory_id),
                    record.agent_id,
                    record.namespace,
                    record.category.value,
                    record.status.value,
                    record.lifecycle.value,
                    record.content_sha256,
                )
                if tuple(row[:7]) != expected:
                    raise MemoryStoreError("Engineer memory index and payload disagree")
                records.append(record)
        except (ValidationError, ValueError) as exc:
            raise MemoryStoreError("Engineer memory contains a malformed record") from exc
        return records

    def logical_sha256(self) -> str:
        payload = {"records": [canonical_json(item) for item in self.load_all()]}
        return canonical_sha256(payload)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.load_all():
            key = f"{item.category.value}/{item.status.value}/{item.lifecycle.value}"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _guard(memory: EngineerMemory) -> None:
        if memory.agent_id != SOFTWARE_ENGINEER_AGENT_ID or memory.namespace != MEMORY_NAMESPACE:
            raise MemoryStoreError("Engineer memory store refuses foreign namespaces")

    @staticmethod
    def _insert(connection: sqlite3.Connection, memory: EngineerMemory) -> None:
        connection.execute(
            """
            INSERT INTO engineer_memories
            (memory_id, agent_id, namespace, category, status, lifecycle, content_sha256,
             created_at, record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(memory.memory_id),
                memory.agent_id,
                memory.namespace,
                memory.category.value,
                memory.status.value,
                memory.lifecycle.value,
                memory.content_sha256,
                memory.created_at.isoformat(),
                canonical_json(memory),
            ),
        )

    @staticmethod
    def _set_lifecycle(
        connection: sqlite3.Connection, memory: EngineerMemory, lifecycle: MemoryLifecycle
    ) -> None:
        updated = memory.model_copy(update={"lifecycle": lifecycle})
        connection.execute(
            """
            UPDATE engineer_memories SET lifecycle = ?, record_json = ?
            WHERE namespace = ? AND memory_id = ?
            """,
            (lifecycle.value, canonical_json(updated), MEMORY_NAMESPACE, str(memory.memory_id)),
        )

    def _connect(self) -> sqlite3.Connection:
        try:
            return sqlite3.connect(self.path, timeout=5)
        except sqlite3.Error as exc:
            raise MemoryStoreError("Engineer memory connection failed") from exc


def observation(
    *,
    cycle_id: UUID,
    sequence: int,
    category: MemoryCategory,
    content: str,
    now: datetime,
    confidence: int = 60,
    tags: Sequence[str] = (),
    status: EpistemicStatus = EpistemicStatus.OBSERVATION,
    evidence_sha256: Sequence[str] = (),
    owner_decision_id: UUID | None = None,
    source: MemorySource = MemorySource.CYCLE_RECORD,
) -> EngineerMemory:
    """Build one memory record with provenance bound to a cycle."""

    return EngineerMemory(
        memory_id=memory_id_for(cycle_id, sequence),
        category=category,
        status=status,
        content=content[:2000],
        tags=list(tags),
        confidence=confidence,
        provenance=MemoryProvenance(
            source=source,
            cycle_id=cycle_id,
            evidence_sha256=list(evidence_sha256),
            owner_decision_id=owner_decision_id,
        ),
        created_at=now,
        valid_from=now,
    )


__all__ = [
    "MEMORY_NAMESPACE",
    "MEMORY_SCHEMA_VERSION",
    "EngineerMemory",
    "EngineerMemoryStore",
    "EpistemicStatus",
    "MemoryCategory",
    "MemoryLifecycle",
    "MemoryProvenance",
    "MemoryQuery",
    "MemorySource",
    "MemoryStoreError",
    "memory_id_for",
    "observation",
]
