"""Operator configuration for the Command Center; every default is local and read-only."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CommandCenterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="NEXUS_COMMAND_CENTER_", extra="ignore"
    )

    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    """Loopback by default. There is no authentication layer, so exposing the dashboard
    beyond this machine is an operator decision that needs one first."""

    port: int = Field(default=8765, ge=1, le=65_535)
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost", "[::1]"], min_length=1
    )
    """``Host`` header values accepted; anything else is refused (DNS rebinding)."""

    engineer_state_root: Path | None = None
    """Defaults to the resident engineer's own ``state_root`` setting."""

    engineer_memory_path: Path | None = None
    """Defaults to the resident engineer's own ``memory_path`` setting."""

    brain_path: Path = Path(".nexus/brain/aegisops-investigator.sqlite3")
    scenario_directory: Path = Path("lab/scenarios/v1")
    client_directory: Path = Path("web/command-center/dist")

    poll_interval_seconds: float = Field(default=1.0, ge=0.25, le=10)
    health_enabled: bool = True
    """Read lab health through the diagnostics layer (fixed local endpoints only)."""

    health_interval_seconds: float = Field(default=15.0, ge=5, le=300)
    replay_enabled: bool = True
    """Re-execute curated engineer evaluation scenarios in a temporary directory at start."""

    engineer_config_visible: bool = True
    """Show the resident engineer's configuration as this process sees it. A container that
    does not share the engineer's environment (it must not: that environment holds its
    secrets) sets this to false, and the dashboard says so instead of showing defaults."""

    max_subscribers: int = Field(default=16, ge=1, le=256)
    max_cycles: int = Field(default=30, ge=1, le=200)


__all__ = ["CommandCenterSettings"]
