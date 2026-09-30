"""Live phase progress for observers of a running cycle (ADR 0013).

While a cycle holds the run lease, ``cycles/active.progress.json`` names the phase the
runtime last entered. It exists only so read-only observers (the Command Center) can show
the real sequence while it happens; nothing in NEXUS reads it back or acts on it, and the
cycle never depends on it. It carries identity, sequence, phase, times, and the kind of
executor in use: no signal, candidate, reason text, model output, path, or credential.

Writes replace the whole file in one step (a private temporary file, then ``os.replace``),
so a reader sees the previous phase or the next one, never half of one. The file belongs
to the cycle named inside it: a cycle removes only its own file when it finishes, however
it finishes, and a cycle that takes the lease removes any leftover from a cycle that died.
A failed write never fails the cycle; progress simply stops being published.
"""

from __future__ import annotations

import contextlib
import os
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, ValidationError

from nexus.atlas.models import StrictModel
from nexus.software_engineer.models import CyclePhase, EngineerIssuer

PROGRESS_FILENAME = "active.progress.json"
_MAX_BYTES = 4096

ExecutorKind = Literal["dry_run", "patchforge", "other"]
_EXECUTOR_KINDS: dict[str, ExecutorKind] = {
    "nexus.software_engineer.cycle.DryRunExecutor": "dry_run",
    "nexus.software_engineer.executor.PatchForgeExecutor": "patchforge",
}


class CycleProgress(StrictModel):
    """The whole progress contract. Observers must treat it as advisory and stale-able."""

    schema_version: Literal[1] = 1
    cycle_id: UUID
    sequence: int = Field(ge=1, le=100)
    phase: CyclePhase
    executor: ExecutorKind
    started_at: AwareDatetime
    updated_at: AwareDatetime
    attested_by: EngineerIssuer = "software_engineer.runtime"


def executor_kind(executor: object) -> ExecutorKind:
    """Identify the executor by its exact class, without importing it (import cycle)."""

    kind = type(executor)
    return _EXECUTOR_KINDS.get(f"{kind.__module__}.{kind.__qualname__}", "other")


def progress_path(state_root: Path) -> Path:
    return state_root / "cycles" / PROGRESS_FILENAME


def read_progress(state_root: Path) -> CycleProgress | None:
    """The published progress, or ``None`` when absent, oversized, or not valid."""

    path = progress_path(state_root)
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        return CycleProgress.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValidationError, ValueError):
        return None


class ProgressPublisher:
    """Publishes one cycle's phases; owned by that cycle and removed by it."""

    def __init__(
        self, state_root: Path, *, cycle_id: UUID, executor: ExecutorKind, started_at: datetime
    ) -> None:
        self.path = progress_path(state_root)
        self.cycle_id = cycle_id
        self.executor: ExecutorKind = executor
        self.started_at = started_at
        self.failed = False

    def discard_stale(self) -> None:
        """Remove a leftover from another cycle. Call only while holding the run lease:
        no other cycle can be running, so any file that is not ours is stale."""

        current = read_progress(self.path.parent.parent)
        if current is not None and current.cycle_id == self.cycle_id:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            self.failed = True

    def publish(self, *, sequence: int, phase: CyclePhase, now: datetime) -> None:
        if self.failed:
            return
        record = CycleProgress(
            cycle_id=self.cycle_id,
            sequence=sequence,
            phase=phase,
            executor=self.executor,
            started_at=self.started_at,
            updated_at=now,
        )
        temporary = self.path.with_name(f".{PROGRESS_FILENAME}.{self.cycle_id.hex}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(record.model_dump_json() + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            self.failed = True
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)

    def finish(self) -> None:
        """Remove the file if it is still ours; never remove another cycle's."""

        current = read_progress(self.path.parent.parent)
        if current is not None and current.cycle_id != self.cycle_id:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            self.failed = True


__all__ = [
    "PROGRESS_FILENAME",
    "CycleProgress",
    "ExecutorKind",
    "ProgressPublisher",
    "executor_kind",
    "progress_path",
    "read_progress",
]
