"""SQLite persistence behind a replaceable private-memory boundary."""

import json
import os
import sqlite3
import unicodedata
from collections import Counter
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from nexus.brain.models import AEGISOPS_AGENT_ID, MemoryRecord, MemoryState, MemoryType


class MemoryStorageError(RuntimeError):
    """The private store is unavailable, corrupt, or violated its typed contract."""


def _normalize(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    return value


def canonical_memory_json(record: MemoryRecord) -> str:
    return json.dumps(
        _normalize(record.model_dump(mode="json")),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def memory_content_sha256(record: MemoryRecord) -> str:
    return sha256(canonical_memory_json(record).encode("utf-8")).hexdigest()


def canonical_snapshot_stream(records: list[MemoryRecord]) -> bytes:
    ordered = sorted(records, key=lambda record: str(record.id))
    payload = "".join(f"{canonical_memory_json(record)}\n" for record in ordered)
    return payload.encode("utf-8")


class SQLiteMemoryStore:
    """Durable local store whose every operation is agent and namespace scoped."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = path
        self.read_only = read_only

    def initialize(self) -> None:
        if self.read_only:
            if not self.path.is_file():
                raise MemoryStorageError("Read-only Brain snapshot is unavailable")
            self._validate_database()
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS private_memories (
                        id TEXT PRIMARY KEY,
                        agent_id TEXT NOT NULL,
                        namespace TEXT NOT NULL,
                        memory_type TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        state TEXT NOT NULL,
                        record_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_private_memories_namespace
                    ON private_memories(agent_id, namespace, state, created_at, id)
                    """
                )
                connection.commit()
        except (OSError, sqlite3.Error) as exc:
            raise MemoryStorageError("Brain storage initialization failed") from exc

    def write(self, record: MemoryRecord) -> bool:
        return bool(self.apply_experience([record], []))

    def apply_experience(
        self,
        records: list[MemoryRecord],
        disputed_ids: list[UUID],
    ) -> list[UUID]:
        """Atomically insert one experience and apply any dispute transitions."""

        for record in records:
            self._validate_namespace(record.namespace)
            if record.agent_id != AEGISOPS_AGENT_ID:
                raise MemoryStorageError("Brain agent identity is invalid")
        if self.read_only:
            raise MemoryStorageError("Brain snapshot is read-only")
        self.initialize()
        written: list[UUID] = []
        try:
            with closing(self._connect()) as connection:
                for record in records:
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO private_memories
                        (id, agent_id, namespace, memory_type, created_at, state, record_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(record.id),
                            record.agent_id,
                            record.namespace,
                            record.memory_type.value,
                            record.created_at.isoformat(),
                            record.state.value,
                            canonical_memory_json(record),
                        ),
                    )
                    if cursor.rowcount == 1:
                        written.append(record.id)
                if disputed_ids:
                    placeholders = ",".join("?" for _ in disputed_ids)
                    rows = connection.execute(
                        f"""
                        SELECT id, record_json FROM private_memories
                        WHERE agent_id = ? AND namespace = ? AND id IN ({placeholders})
                        """,
                        [
                            AEGISOPS_AGENT_ID,
                            AEGISOPS_AGENT_ID,
                            *(str(item) for item in disputed_ids),
                        ],
                    ).fetchall()
                    for memory_id, payload in rows:
                        disputed = MemoryRecord.model_validate_json(payload).model_copy(
                            update={"state": MemoryState.DISPUTED}
                        )
                        connection.execute(
                            """
                            UPDATE private_memories SET state = ?, record_json = ?
                            WHERE agent_id = ? AND namespace = ? AND id = ?
                            """,
                            (
                                MemoryState.DISPUTED.value,
                                canonical_memory_json(disputed),
                                AEGISOPS_AGENT_ID,
                                AEGISOPS_AGENT_ID,
                                memory_id,
                            ),
                        )
                connection.commit()
            return written
        except (sqlite3.Error, ValidationError) as exc:
            raise MemoryStorageError("Brain experience transaction failed") from exc

    def load_active(self, namespace: str) -> list[MemoryRecord]:
        return self._load(namespace, active_only=True)

    def load_all(self, namespace: str) -> list[MemoryRecord]:
        return self._load(namespace, active_only=False)

    def _load(self, namespace: str, *, active_only: bool) -> list[MemoryRecord]:
        self._validate_namespace(namespace)
        self.initialize()
        where = "AND state = ?" if active_only else ""
        parameters: list[str] = [AEGISOPS_AGENT_ID, namespace]
        if active_only:
            parameters.append(MemoryState.ACTIVE.value)
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"""
                    SELECT id, agent_id, namespace, memory_type, state, record_json
                    FROM private_memories
                    WHERE agent_id = ? AND namespace = ? {where}
                    ORDER BY id ASC
                    """,
                    parameters,
                ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryStorageError("Brain memory read failed") from exc
        records: list[MemoryRecord] = []
        try:
            for row in rows:
                record = MemoryRecord.model_validate_json(row[5])
                expected = (
                    str(record.id),
                    record.agent_id,
                    record.namespace,
                    record.memory_type.value,
                    record.state.value,
                )
                if tuple(row[:5]) != expected:
                    raise MemoryStorageError("Brain index and record payload disagree")
                records.append(record)
        except (ValidationError, ValueError) as exc:
            raise MemoryStorageError("Brain contains a malformed memory record") from exc
        return records

    def supersede(self, namespace: str, memory_ids: list[UUID]) -> int:
        return self.set_state(namespace, memory_ids, MemoryState.SUPERSEDED)

    def set_state(self, namespace: str, memory_ids: list[UUID], state: MemoryState) -> int:
        self._validate_namespace(namespace)
        if self.read_only:
            raise MemoryStorageError("Brain snapshot is read-only")
        self.initialize()
        if not memory_ids:
            return 0
        placeholders = ",".join("?" for _ in memory_ids)
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"""
                    SELECT id, record_json FROM private_memories
                    WHERE agent_id = ? AND namespace = ? AND id IN ({placeholders})
                    """,
                    [AEGISOPS_AGENT_ID, namespace, *(str(item) for item in memory_ids)],
                ).fetchall()
                for memory_id, payload in rows:
                    record = MemoryRecord.model_validate_json(payload).model_copy(
                        update={"state": state}
                    )
                    connection.execute(
                        """
                        UPDATE private_memories SET state = ?, record_json = ?
                        WHERE agent_id = ? AND namespace = ? AND id = ?
                        """,
                        [
                            state.value,
                            canonical_memory_json(record),
                            AEGISOPS_AGENT_ID,
                            namespace,
                            memory_id,
                        ],
                    )
                connection.commit()
                return len(rows)
        except (sqlite3.Error, ValidationError) as exc:
            raise MemoryStorageError("Brain lifecycle update failed") from exc

    def logical_sha256(self, namespace: str) -> str:
        return sha256(canonical_snapshot_stream(self.load_all(namespace))).hexdigest()

    def memory_type_counts(self, namespace: str) -> dict[MemoryType, int]:
        counts = Counter(record.memory_type for record in self.load_all(namespace))
        return {memory_type: counts[memory_type] for memory_type in MemoryType}

    def snapshot(self, destination: Path) -> tuple[str, Path, str]:
        """Export a SQLite copy and canonical JSONL evidence stream."""

        self.initialize()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.unlink(missing_ok=True)
        canonical_path = destination.with_suffix(f"{destination.suffix}.jsonl")
        try:
            with closing(self._connect()) as source, closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
                target.commit()
            os.replace(temporary, destination)
            stream = canonical_snapshot_stream(self.load_all(AEGISOPS_AGENT_ID))
            canonical_temporary = canonical_path.with_name(f".{canonical_path.name}.tmp")
            canonical_temporary.write_bytes(stream)
            os.replace(canonical_temporary, canonical_path)
            return (
                sha256(destination.read_bytes()).hexdigest(),
                canonical_path,
                sha256(stream).hexdigest(),
            )
        except (OSError, sqlite3.Error) as exc:
            raise MemoryStorageError("Brain snapshot failed") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate_namespace(namespace: str) -> None:
        if namespace != AEGISOPS_AGENT_ID:
            raise MemoryStorageError("Brain namespace is invalid")

    def _validate_database(self) -> None:
        try:
            with closing(self._connect()) as connection:
                result = connection.execute("PRAGMA quick_check").fetchone()
                if result is None or result[0] != "ok":
                    raise MemoryStorageError("Brain snapshot failed integrity validation")
                connection.execute("SELECT 1 FROM private_memories LIMIT 1").fetchone()
        except sqlite3.Error as exc:
            raise MemoryStorageError("Brain snapshot is invalid") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            if self.read_only:
                uri = f"{self.path.resolve().as_uri()}?mode=ro"
                return sqlite3.connect(uri, uri=True, timeout=5)
            return sqlite3.connect(self.path, timeout=5)
        except sqlite3.Error as exc:
            raise MemoryStorageError("Brain database connection failed") from exc
