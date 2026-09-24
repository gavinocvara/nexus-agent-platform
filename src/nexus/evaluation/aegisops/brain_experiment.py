"""Evaluator-only contamination ledger and held-out snapshot guard."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from nexus.brain.storage import SQLiteMemoryStore


class ExperimentArm(StrEnum):
    MEMORYLESS = "contemporaneous_memoryless"
    FROZEN_BRAIN = "held_out_frozen_brain"
    PLACEBO = "length_matched_placebo"


class ContaminationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: UUID
    training_scenario_id: str = Field(min_length=1, max_length=100)


class BrainContaminationLedger(BaseModel):
    """Evaluator-owned mapping that is never passed into Brain."""

    model_config = ConfigDict(extra="forbid")

    protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    entries: list[ContaminationEntry]


def verify_held_out_snapshot(
    store: SQLiteMemoryStore,
    namespace: str,
    held_out_scenario_id: str,
    ledger: BrainContaminationLedger,
) -> None:
    """Refuse a fold if any active record originated from its held-out scenario."""

    active_ids = {record.id for record in store.load_active(namespace)}
    contaminated = {
        entry.memory_id
        for entry in ledger.entries
        if entry.training_scenario_id == held_out_scenario_id and entry.memory_id in active_ids
    }
    if contaminated:
        raise ValueError("Frozen Brain snapshot contains held-out scenario memory")
