"""Transactional SQLite persistence for Atlas jobs, commands, and audit events."""

import json
import sqlite3
import unicodedata
from collections.abc import Mapping
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from nexus.atlas.models import AuditEvent, CommandRecord, Job


class AtlasStorageError(RuntimeError):
    """Atlas persistence is unavailable, corrupt, or inconsistent."""


class JobNotFoundError(AtlasStorageError):
    """Requested Atlas job does not exist."""


class IdempotencyConflictError(AtlasStorageError):
    """A command ID was reused for a different request."""


class ConcurrencyError(AtlasStorageError):
    """A job revision changed before the command could commit."""


def _normalize(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    return value


def canonical_json(value: BaseModel | Mapping[str, Any]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    return json.dumps(
        _normalize(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def canonical_sha256(value: BaseModel | Mapping[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


class SQLiteAtlasStore:
    """Single-node durable store with optimistic revisions and command replay."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS atlas_jobs (
                        job_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        updated_at TEXT NOT NULL,
                        record_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS atlas_audit_events (
                        event_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        event_json TEXT NOT NULL,
                        UNIQUE(job_id, sequence),
                        FOREIGN KEY(job_id) REFERENCES atlas_jobs(job_id)
                    );
                    CREATE TABLE IF NOT EXISTS atlas_commands (
                        command_id TEXT PRIMARY KEY,
                        operation TEXT NOT NULL,
                        request_sha256 TEXT NOT NULL,
                        job_id TEXT NOT NULL,
                        response_revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        response_json TEXT NOT NULL,
                        FOREIGN KEY(job_id) REFERENCES atlas_jobs(job_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_atlas_jobs_status
                    ON atlas_jobs(status, updated_at, job_id);
                    CREATE INDEX IF NOT EXISTS idx_atlas_events_job
                    ON atlas_audit_events(job_id, sequence);
                    CREATE TRIGGER IF NOT EXISTS atlas_audit_events_no_update
                    BEFORE UPDATE ON atlas_audit_events
                    BEGIN
                        SELECT RAISE(ABORT, 'Atlas audit events are append-only');
                    END;
                    CREATE TRIGGER IF NOT EXISTS atlas_audit_events_no_delete
                    BEFORE DELETE ON atlas_audit_events
                    BEGIN
                        SELECT RAISE(ABORT, 'Atlas audit events are append-only');
                    END;
                    CREATE TRIGGER IF NOT EXISTS atlas_commands_no_update
                    BEFORE UPDATE ON atlas_commands
                    BEGIN
                        SELECT RAISE(ABORT, 'Atlas command records are immutable');
                    END;
                    CREATE TRIGGER IF NOT EXISTS atlas_commands_no_delete
                    BEFORE DELETE ON atlas_commands
                    BEGIN
                        SELECT RAISE(ABORT, 'Atlas command records are immutable');
                    END;
                    """
                )
                connection.commit()
        except (OSError, sqlite3.Error) as exc:
            raise AtlasStorageError("Atlas storage initialization failed") from exc

    def get_job(self, job_id: UUID) -> Job:
        self.initialize()
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT job_id, status, revision, updated_at, record_json "
                    "FROM atlas_jobs WHERE job_id = ?",
                    (str(job_id),),
                ).fetchone()
        except sqlite3.Error as exc:
            raise AtlasStorageError("Atlas job read failed") from exc
        if row is None:
            raise JobNotFoundError(f"Atlas job not found: {job_id}")
        return self._job_from_row(row)

    def list_jobs(self) -> list[Job]:
        self.initialize()
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT job_id, status, revision, updated_at, record_json "
                    "FROM atlas_jobs ORDER BY updated_at, job_id"
                ).fetchall()
        except sqlite3.Error as exc:
            raise AtlasStorageError("Atlas job listing failed") from exc
        return [self._job_from_row(row) for row in rows]

    def list_audit_events(self, job_id: UUID) -> list[AuditEvent]:
        self.initialize()
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT event_id, job_id, sequence, event_type, occurred_at, event_json "
                    "FROM atlas_audit_events WHERE job_id = ? ORDER BY sequence",
                    (str(job_id),),
                ).fetchall()
        except sqlite3.Error as exc:
            raise AtlasStorageError("Atlas audit read failed") from exc
        events: list[AuditEvent] = []
        try:
            for row in rows:
                event = AuditEvent.model_validate_json(row[5])
                expected = (
                    str(event.event_id),
                    str(event.job_id),
                    event.sequence,
                    event.event_type.value,
                    event.occurred_at.isoformat(),
                )
                if tuple(row[:5]) != expected:
                    raise AtlasStorageError("Atlas audit index and payload disagree")
                events.append(event)
        except (ValidationError, ValueError) as exc:
            raise AtlasStorageError("Atlas contains a malformed audit event") from exc
        return events

    def lookup_command(
        self,
        command_id: str,
        operation: str,
        request_sha256: str,
    ) -> Job | None:
        self.initialize()
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT operation, request_sha256, job_id, response_revision, response_json "
                    "FROM atlas_commands WHERE command_id = ?",
                    (command_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise AtlasStorageError("Atlas command read failed") from exc
        if row is None:
            return None
        return self._command_response(row, operation, request_sha256)

    def create(self, job: Job, event: AuditEvent, command: CommandRecord) -> Job:
        if job.revision != 0 or event.sequence != 1 or event.job_revision != 0:
            raise AtlasStorageError("Atlas creation revision is invalid")
        return self._persist(None, job, event, command)

    def update(
        self,
        expected_revision: int,
        job: Job,
        event: AuditEvent,
        command: CommandRecord,
    ) -> Job:
        if job.revision != expected_revision + 1:
            raise AtlasStorageError("Atlas update revision is invalid")
        return self._persist(expected_revision, job, event, command)

    def integrity_check(self) -> None:
        self.initialize()
        try:
            with closing(self._connect()) as connection:
                result = connection.execute("PRAGMA quick_check").fetchone()
                if result is None or result[0] != "ok":
                    raise AtlasStorageError("Atlas database failed integrity validation")
                command_rows = connection.execute(
                    "SELECT operation, request_sha256, job_id, response_revision, response_json "
                    "FROM atlas_commands ORDER BY command_id"
                ).fetchall()
        except sqlite3.Error as exc:
            raise AtlasStorageError("Atlas integrity check failed") from exc
        jobs = self.list_jobs()
        for job in jobs:
            events = self.list_audit_events(job.job_id)
            if (
                not events
                or len(events) != job.revision + 1
                or events[-1].job_revision != job.revision
            ):
                raise AtlasStorageError("Atlas job and audit history revisions disagree")
        for row in command_rows:
            self._command_response(row, str(row[0]), str(row[1]))

    def _persist(
        self,
        expected_revision: int | None,
        job: Job,
        event: AuditEvent,
        command: CommandRecord,
    ) -> Job:
        if event.job_id != job.job_id or command.job_id != job.job_id:
            raise AtlasStorageError("Atlas transaction identities disagree")
        if event.job_revision != job.revision or command.response_revision != job.revision:
            raise AtlasStorageError("Atlas transaction revisions disagree")
        self.initialize()
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT operation, request_sha256, job_id, response_revision, response_json "
                    "FROM atlas_commands WHERE command_id = ?",
                    (command.command_id,),
                ).fetchone()
                if existing is not None:
                    response = self._command_response(
                        existing,
                        command.operation,
                        command.request_sha256,
                    )
                    connection.rollback()
                    return response
                job_json = canonical_json(job)
                if expected_revision is None:
                    connection.execute(
                        "INSERT INTO atlas_jobs "
                        "(job_id, status, revision, updated_at, record_json) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            str(job.job_id),
                            job.status.value,
                            job.revision,
                            job.updated_at.isoformat(),
                            job_json,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        "UPDATE atlas_jobs SET status = ?, revision = ?, updated_at = ?, "
                        "record_json = ? WHERE job_id = ? AND revision = ?",
                        (
                            job.status.value,
                            job.revision,
                            job.updated_at.isoformat(),
                            job_json,
                            str(job.job_id),
                            expected_revision,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ConcurrencyError("Atlas job revision changed")
                connection.execute(
                    "INSERT INTO atlas_audit_events "
                    "(event_id, job_id, sequence, event_type, occurred_at, event_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(event.event_id),
                        str(event.job_id),
                        event.sequence,
                        event.event_type.value,
                        event.occurred_at.isoformat(),
                        canonical_json(event),
                    ),
                )
                connection.execute(
                    "INSERT INTO atlas_commands "
                    "(command_id, operation, request_sha256, job_id, response_revision, "
                    "created_at, response_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        command.command_id,
                        command.operation,
                        command.request_sha256,
                        str(command.job_id),
                        command.response_revision,
                        command.created_at.isoformat(),
                        job_json,
                    ),
                )
                connection.commit()
                return job
        except (ConcurrencyError, IdempotencyConflictError):
            raise
        except (sqlite3.Error, OSError) as exc:
            raise AtlasStorageError("Atlas transaction failed") from exc

    @staticmethod
    def _command_response(
        row: sqlite3.Row | tuple[object, ...],
        operation: str,
        request_sha256: str,
    ) -> Job:
        if row[0] != operation or row[1] != request_sha256:
            raise IdempotencyConflictError("Atlas command ID was reused with different input")
        try:
            job = Job.model_validate_json(str(row[4]))
        except (ValidationError, ValueError) as exc:
            raise AtlasStorageError("Atlas command contains a malformed response") from exc
        if str(job.job_id) != row[2] or job.revision != row[3]:
            raise AtlasStorageError("Atlas command index and response disagree")
        return job

    @staticmethod
    def _job_from_row(row: sqlite3.Row | tuple[object, ...]) -> Job:
        try:
            job = Job.model_validate_json(str(row[4]))
        except (ValidationError, ValueError) as exc:
            raise AtlasStorageError("Atlas contains a malformed job") from exc
        expected = (str(job.job_id), job.status.value, job.revision, job.updated_at.isoformat())
        if tuple(row[:4]) != expected:
            raise AtlasStorageError("Atlas job index and payload disagree")
        return job

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            return connection
        except sqlite3.Error as exc:
            raise AtlasStorageError("Atlas database connection failed") from exc
