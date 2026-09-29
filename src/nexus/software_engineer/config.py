"""Typed, default-off configuration for the resident Software Engineer.

Nothing here spends money or changes code on a fresh clone: ``enabled`` is ``False``, the
mode is ``dry_run``, and no model is configured. Budgets, mode, and the owner identity are
governing settings: the engineer reads them and never writes them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nexus.atlas.models import Identifier
from nexus.software_engineer.issues import GITHUB_READ_TOKEN_ENV_DEFAULT
from nexus.software_engineer.models import CycleBudget, CycleMode
from nexus.software_engineer.pricing import PriceTable
from nexus.software_engineer.publish import GITHUB_TOKEN_ENV_DEFAULT

SLACK_WEBHOOK_ENV_DEFAULT = "NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_URL"
SLACK_SIGNING_SECRET_ENV_DEFAULT = "NEXUS_SOFTWARE_ENGINEER_SLACK_SIGNING_SECRET"


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

    repository_url: str = Field(
        default="https://github.com/gavinocvara/nexus-agent-platform", min_length=8, max_length=500
    )
    """Operator identity of the repository the engineer maintains (no credentials)."""

    sandbox: Literal["none", "local_process"] = "none"
    """``local_process`` runs operator commands as bounded local processes; only for
    ephemeral, credential-free runners. ``none`` leaves the engineer in plan-only mode."""

    state_root: Path = Path(".nexus/software_engineer")
    memory_path: Path = Path(".nexus/software_engineer/memory.sqlite3")
    model: str | None = Field(default=None, min_length=1, max_length=100)
    """No model is configured by default; mechanical recipes need none."""

    confirm_model_spend: bool = False
    """Explicit acknowledgement that model recipes may spend money. Without it, and
    without a model, a positive model-call budget, and an API key, model recipes stay
    approval-only plans."""

    model_timeout_seconds: float = Field(default=60, gt=0, le=600)
    model_max_output_tokens: int = Field(default=1024, ge=64, le=32_000)

    model_price_input_per_mtok: float | None = Field(default=None, ge=0, le=10_000)
    model_price_output_per_mtok: float | None = Field(default=None, ge=0, le=10_000)
    """USD per million input / output tokens for ``model``. Both are required, together
    with a positive ``max_cost_usd``, before a model recipe may spend: the engineer never
    guesses a price, and a cost budget it cannot measure is not a budget."""

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

    slack_signing_secret_env: str = Field(
        default=SLACK_SIGNING_SECRET_ENV_DEFAULT, min_length=1, max_length=100
    )
    """Name of the variable holding the Slack app signing secret that authenticates slash
    commands. Read per request, never stored."""

    slack_owner_user_id: str | None = Field(default=None, min_length=1, max_length=50)
    """The one Slack member id whose ``/nexus`` commands count as the owner's. Unset means
    no Slack command is ever accepted."""

    slack_replay_window_seconds: int = Field(default=300, ge=30, le=900)

    github_token_env: str = Field(default=GITHUB_TOKEN_ENV_DEFAULT, min_length=1, max_length=100)
    """Name of the environment variable holding a fine-grained GitHub token that may create
    branches and draft pull requests on the repository (contents and pull requests: write,
    nothing else). It is read only while publishing and never stored, logged, or remembered.
    The scheduled workflow does not receive it."""

    read_issues: bool = False
    """Read open GitHub issues (never pull requests) as untrusted signals. Off by default
    because it is the engineer's only network read; the scheduled workflow passes its
    read-only token so the read works on private repositories."""

    github_read_token_env: str = Field(
        default=GITHUB_READ_TOKEN_ENV_DEFAULT, min_length=1, max_length=100
    )
    """Name of the environment variable holding a read-only token for issue intake
    (``issues: read``). Optional: public repositories can be read without one."""

    max_issues: int = Field(default=20, ge=1, le=100)

    publish_from_cycle: bool = False
    """Let an ``autonomous_low_risk`` cycle open a draft pull request itself when the token is
    present. Off, every cycle only proposes; publishing happens through ``decide`` and
    ``publish`` after the owner's SHIP. A draft pull request is never a merge."""
    max_notifications_per_cycle: int = Field(default=5, ge=1, le=50)
    schedule_cron: str = Field(default="17 6 * * *", min_length=9, max_length=100)
    """Documented intent only; the GitHub Actions workflow owns the real schedule."""

    @field_validator(
        "model",
        "slack_owner_user_id",
        "model_price_input_per_mtok",
        "model_price_output_per_mtok",
        mode="before",
    )
    @classmethod
    def empty_model_means_none(cls, value: object) -> object:
        """CI passes unset variables as empty strings; an empty model is no model."""

        if isinstance(value, str) and not value.strip():
            return None
        return value

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

    @property
    def price_table(self) -> PriceTable | None:
        if self.model_price_input_per_mtok is None or self.model_price_output_per_mtok is None:
            return None
        return PriceTable(
            input_usd_per_mtok=self.model_price_input_per_mtok,
            output_usd_per_mtok=self.model_price_output_per_mtok,
        )

    @property
    def model_recipes_allowed(self) -> bool:
        """True only when every explicit condition for spending on a model holds:
        a model, a confirmed spend, positive call and token budgets, both prices, and a
        positive cost budget."""

        return (
            self.model is not None
            and self.confirm_model_spend
            and self.max_model_calls > 0
            and self.max_output_tokens > 0
            and self.price_table is not None
            and self.max_cost_usd is not None
            and self.max_cost_usd > 0
        )

    def require_enabled(self) -> None:
        if not self.enabled:
            raise EngineerDisabledError(
                "Set NEXUS_SOFTWARE_ENGINEER_ENABLED=true to run the resident engineer"
            )


__all__ = [
    "GITHUB_READ_TOKEN_ENV_DEFAULT",
    "GITHUB_TOKEN_ENV_DEFAULT",
    "SLACK_SIGNING_SECRET_ENV_DEFAULT",
    "SLACK_WEBHOOK_ENV_DEFAULT",
    "EngineerDisabledError",
    "SoftwareEngineerSettings",
]
