"""Typed configuration for the agent-private Brain boundary."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from nexus.brain.models import BrainMode


class BrainSettings(BaseSettings):
    """Local Brain settings with an explicit experiment mode."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NEXUS_BRAIN_",
        extra="ignore",
    )

    mode: BrainMode = BrainMode.DISABLED
    namespace: Literal["aegisops.investigator"] = "aegisops.investigator"
    path: Path = Path(".nexus/brain/aegisops-investigator.sqlite3")
    max_retrieved_memories: int = Field(default=3, ge=1, le=8)
    max_context_tokens: int = Field(default=800, ge=100, le=4000)
    max_context_chars: int = Field(default=3200, ge=400, le=16000)
    max_age_days: int = Field(default=365, ge=1, le=3650)
    retrieval_timeout_seconds: float = Field(default=2.0, gt=0, le=10)
    expected_snapshot_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    preregistration_path: Path = Path("docs/experiments/brain-v1-protocol.json")

    @property
    def enabled(self) -> bool:
        return self.mode is not BrainMode.DISABLED

    @property
    def read_only(self) -> bool:
        return self.mode is BrainMode.FROZEN_EVAL
