"""Read-only access to the records the Command Center displays.

Nothing here creates, modifies, locks, or deletes a file. Records are validated with the
same models the runtime writes them with; SQLite stores are opened ``mode=ro``; lab health
goes through the bounded diagnostics layer. A source that is missing reports ``absent``,
one that fails validation reports ``unreadable``: the dashboard shows the gap, it never
fills it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from nexus.diagnostics.config import DiagnosticsSettings
from nexus.diagnostics.models import SystemHealthResult
from nexus.diagnostics.service import DiagnosticServiceLayer
from nexus.lab.catalog import ScenarioCatalog, ScenarioCatalogError
from nexus.sentinelqa.models import SentinelVerdict
from nexus.software_engineer.memory import EngineerMemory
from nexus.software_engineer.models import CycleRecord, OwnerDecision, PublishedChange

LEASE_FILENAME = "run.lock"
PROGRESS_FILENAME = "active.progress.json"
"""Proposed instrumentation (ADR 0013); read when present, never written here."""

_MAX_RECORD_BYTES = 5_000_000
_MAX_CYCLE_FILES = 400


@dataclass(frozen=True, slots=True)
class LeaseInfo:
    cycle_id: UUID
    started_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ProgressInfo:
    cycle_id: UUID
    phase: str


@dataclass(frozen=True, slots=True)
class InterruptedInfo:
    cycle_id: UUID
    started_at: datetime | None
    reason: str
    recovered_at: datetime | None


@dataclass(slots=True)
class _Cached:
    stamp: tuple[int, int]
    record: CycleRecord


@dataclass(slots=True)
class EngineerStateReader:
    """The resident engineer's state tree, read without side effects."""

    state_root: Path
    _cache: dict[Path, _Cached] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def cycles_dir(self) -> Path:
        return self.state_root / "cycles"

    def exists(self) -> bool:
        return self.state_root.is_dir()

    def lease(self) -> LeaseInfo | None:
        data = _read_json(self.state_root / LEASE_FILENAME)
        if data is None:
            return None
        try:
            return LeaseInfo(
                cycle_id=UUID(str(data["cycle_id"])),
                started_at=datetime.fromisoformat(str(data["started_at"])),
                expires_at=datetime.fromisoformat(str(data["expires_at"])),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def progress(self) -> ProgressInfo | None:
        data = _read_json(self.cycles_dir / PROGRESS_FILENAME)
        if data is None:
            return None
        try:
            return ProgressInfo(cycle_id=UUID(str(data["cycle_id"])), phase=str(data["phase"]))
        except (KeyError, TypeError, ValueError):
            return None

    def interrupted(self) -> list[InterruptedInfo]:
        found: list[InterruptedInfo] = []
        if not self.cycles_dir.is_dir():
            return found
        for path in sorted(self.cycles_dir.glob("*.interrupted.json"))[-20:]:
            data = _read_json(path)
            if data is None:
                continue
            try:
                found.append(
                    InterruptedInfo(
                        cycle_id=UUID(str(data["cycle_id"])),
                        started_at=_optional_time(data.get("started_at")),
                        reason=str(data.get("reason", "unknown")),
                        recovered_at=_optional_time(data.get("recovered_at")),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return found

    def records(self, limit: int) -> list[CycleRecord]:
        """Validated cycle records, newest completion first."""

        self.problems = []
        if not self.cycles_dir.is_dir():
            return []
        paths = [
            path
            for path in self.cycles_dir.glob("*.json")
            if path.name != "latest.json"
            and not path.name.endswith(".interrupted.json")
            and path.name != PROGRESS_FILENAME
            and not path.name.startswith(".")
        ]
        paths.sort(key=_mtime, reverse=True)
        records: list[CycleRecord] = []
        live = set(paths[:_MAX_CYCLE_FILES])
        for path in paths[:_MAX_CYCLE_FILES]:
            record = self._load(path)
            if record is not None:
                records.append(record)
        for stale in set(self._cache) - live:
            del self._cache[stale]
        records.sort(key=lambda item: (item.completed_at, str(item.cycle_id)), reverse=True)
        return records[:limit]

    def record(self, cycle_id: UUID) -> CycleRecord | None:
        return self._load(self.cycles_dir / f"{cycle_id}.json")

    def report_text(self, cycle_id: UUID) -> str | None:
        path = self.cycles_dir / f"{cycle_id}.report.md"
        try:
            if path.stat().st_size > _MAX_RECORD_BYTES:
                return None
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def decision(self, request_id: UUID) -> OwnerDecision | None:
        return _load_model(self.state_root / "decisions" / f"{request_id}.json", OwnerDecision)

    def publication(self, request_id: UUID) -> PublishedChange | None:
        return _load_model(self.state_root / "publications" / f"{request_id}.json", PublishedChange)

    def sentinel_verdict(self, cycle_id: UUID, candidate_id: UUID) -> SentinelVerdict | None:
        path = (
            self.state_root
            / "runs"
            / str(cycle_id)
            / candidate_id.hex[:12]
            / "sentinel_verdict.json"
        )
        return _load_model(path, SentinelVerdict)

    def fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        """Cheap change detection: names, sizes, and modification times only."""

        entries: list[tuple[str, int, int]] = []
        for directory in (
            self.state_root,
            self.cycles_dir,
            self.state_root / "decisions",
            self.state_root / "publications",
        ):
            if not directory.is_dir():
                continue
            for path in directory.iterdir():
                if path.name.startswith(".") or not path.is_file():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                entries.append((str(path), stat.st_size, stat.st_mtime_ns))
        return tuple(sorted(entries))

    def _load(self, path: Path) -> CycleRecord | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        stamp = (stat.st_size, stat.st_mtime_ns)
        cached = self._cache.get(path)
        if cached is not None and cached.stamp == stamp:
            return cached.record
        if stat.st_size > _MAX_RECORD_BYTES:
            self.problems.append(f"{path.name}: larger than the display bound")
            return None
        try:
            record = CycleRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError, ValueError):
            self.problems.append(f"{path.name}: not a valid cycle record")
            return None
        self._cache[path] = _Cached(stamp=stamp, record=record)
        return record


@dataclass(frozen=True, slots=True)
class EngineerMemoryRead:
    counts: list[tuple[str, str, str, int]]
    recent: list[EngineerMemory]


def read_engineer_memory(path: Path, *, recent: int = 8) -> EngineerMemoryRead | None:
    """Counts and the newest records of the engineer's private memory; ``None`` if absent.

    Raises ``sqlite3.Error`` or ``ValueError`` when the store exists but cannot be read.
    """

    if not path.is_file():
        return None
    with _read_only(path) as connection:
        counts = [
            (str(row[0]), str(row[1]), str(row[2]), int(row[3]))
            for row in connection.execute(
                """
                SELECT category, status, lifecycle, COUNT(*) FROM engineer_memories
                WHERE namespace = 'software_engineer.resident'
                GROUP BY category, status, lifecycle
                """
            )
        ]
        rows = connection.execute(
            """
            SELECT record_json FROM engineer_memories
            WHERE namespace = 'software_engineer.resident'
            ORDER BY created_at DESC, memory_id DESC LIMIT ?
            """,
            (recent,),
        ).fetchall()
    records = [EngineerMemory.model_validate_json(str(row[0])) for row in rows]
    return EngineerMemoryRead(counts=counts, recent=records)


def read_brain_counts(path: Path) -> list[tuple[str, str, int]] | None:
    """``(memory_type, state, count)`` for the AegisOps investigator; ``None`` if absent."""

    if not path.is_file():
        return None
    with _read_only(path) as connection:
        return [
            (str(row[0]), str(row[1]), int(row[2]))
            for row in connection.execute(
                """
                SELECT memory_type, state, COUNT(*) FROM private_memories
                WHERE agent_id = 'aegisops.investigator'
                GROUP BY memory_type, state
                """
            )
        ]


def read_system_health(settings: DiagnosticsSettings | None = None) -> SystemHealthResult:
    """One bounded read of lab health through the diagnostics layer's fixed endpoints."""

    layer = DiagnosticServiceLayer(settings)
    try:
        return layer.get_system_health()
    finally:
        layer.close()


@dataclass(frozen=True, slots=True)
class LabScenario:
    id: str
    title: str
    target_service: str


def read_lab_scenarios(directory: Path) -> list[LabScenario] | None:
    """Operator-facing identity of the lab incidents; ground truth is not copied out."""

    try:
        catalog = ScenarioCatalog.load(directory)
    except ScenarioCatalogError:
        return None
    return [
        LabScenario(id=item.id, title=item.title, target_service=str(item.target.service))
        for item in catalog.list()
    ]


@contextmanager
def _read_only(path: Path) -> Iterator[sqlite3.Connection]:
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as connection:
        yield connection


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        if path.stat().st_size > 64_000:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_model[ModelT: (OwnerDecision, PublishedChange, SentinelVerdict)](
    path: Path, model: type[ModelT]
) -> ModelT | None:
    try:
        if path.stat().st_size > _MAX_RECORD_BYTES:
            return None
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValidationError, ValueError):
        return None


def _optional_time(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


__all__ = [
    "LEASE_FILENAME",
    "PROGRESS_FILENAME",
    "EngineerMemoryRead",
    "EngineerStateReader",
    "InterruptedInfo",
    "LabScenario",
    "LeaseInfo",
    "ProgressInfo",
    "read_brain_counts",
    "read_engineer_memory",
    "read_lab_scenarios",
    "read_system_health",
]
