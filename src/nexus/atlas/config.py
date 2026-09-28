"""Configuration for the local Atlas control plane."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AtlasSettings(BaseSettings):
    """Local-first Atlas settings with no ambient runtime credentials."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NEXUS_ATLAS_",
        extra="ignore",
    )

    database_path: Path = Path(".nexus/atlas/atlas.sqlite3")
