"""Typed, default-off configuration for the resident Software Engineer.

Nothing here spends money or changes code on a fresh clone: ``enabled`` is ``False``, the
mode is ``dry_run``, and no model is configured. Budgets, mode, and the owner identity are
governing settings: the engineer reads them and never writes them.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from nexus.atlas.models import Identifier
from nexus.software_engineer.models import CycleBudget, CycleMode

SLACK_WEBHOOK_ENV_DEFAULT = "NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_URL"


class EngineerDisabledError(RuntimeError):
    """The resident engineer was asked to act while disabled."""


class SoftwareEngineerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="NEXUS_SOFTWARE_ENGINEER_", extra="ignore"
    )

    enabled: bool = False
    mode: CycleMode = CycleMode.DRY_RUN
    owner_id: Identifier = "owner"
    """Atlas human actor id whose decisions the engineer accepts."""

    state_root: Path = Path(".nexus/software_engineer")
    memory_path: Path = Path(".nexus/software_engineer/memory.sqlite3")
    model: str | None = Field(default=None, min_length=1, max_length=100)
    """No model is configured by default; the deterministic engine needs none."""

    max_runtime_seconds: int = Field(default=1800, ge=60, le=86_400)
    max_turns: int = Field(default=40, ge=1, le=1000)
    max_tool_calls: int = Field(default=200, ge=1, le=10_000)
    max_model_calls: int = Field(default=0, ge=0, le=1000)
    max_input_tokens: int = Field(default=0, ge=0, le=50_000_000)
    max_output_tokens: int = Field(default=0, ge=0, le=5_000_000)
    max_cost_usd: float | None = Field(default=0.0, ge=0)
    max_changed_files: int = Field(default=5, ge=1, le=100)
    max_diff_bytes: int = Field(default=20_000, ge=1, le=10_000_000)

    slack_webhook_env: str = Field(default=SLACK_WEBHOOK_ENV_DEFAULT, min_length=1, max_length=100)
    """Name of the environment variable holding the Slack webhook URL. The value is read
    only when a message is sent and is never stored, logged, or remembered."""

    slack_channel_label: str = Field(default="#nexus-engineering", min_length=1, max_length=100)
    max_notifications_per_cycle: int = Field(default=5, ge=1, le=50)
    schedule_cron: str = Field(default="17 6 * * *", min_length=9, max_length=100)
    """Documented intent only; the GitHub Actions workflow owns the real schedule."""

    @property
    def budget(self) -> CycleBudget:
        return CycleBudget(
            max_runtime_seconds=self.max_runtime_seconds,
            max_turns=self.max_turns,
            max_tool_calls=self.max_tool_calls,
            max_model_calls=self.max_model_calls,
            max_input_tokens=self.max_input_tokens,
            max_output_tokens=self.max_output_tokens,
            max_cost_usd=self.max_cost_usd,
            max_changed_files=self.max_changed_files,
            max_diff_bytes=self.max_diff_bytes,
        )

    def require_enabled(self) -> None:
        if not self.enabled:
            raise EngineerDisabledError(
                "Set NEXUS_SOFTWARE_ENGINEER_ENABLED=true to run the resident engineer"
            )


__all__ = ["SLACK_WEBHOOK_ENV_DEFAULT", "EngineerDisabledError", "SoftwareEngineerSettings"]
