"""Shared configuration primitives for NEXUS."""

from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class NexusEnvironment(StrEnum):
    """Supported runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class NexusSettings(BaseSettings):
    """Process-level settings shared by early NEXUS components."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NEXUS_",
        extra="ignore",
    )

    env: NexusEnvironment = NexusEnvironment.DEVELOPMENT
    log_level: str = Field(default="INFO", min_length=1)


def load_settings() -> NexusSettings:
    """Load NEXUS settings from environment and local .env overrides."""

    return NexusSettings()
