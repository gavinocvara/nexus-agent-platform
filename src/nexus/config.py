"""Shared configuration primitives for NEXUS."""

from enum import StrEnum
from typing import Literal

from pydantic import AnyHttpUrl, Field
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
    lab_failures_enabled: bool = False


class GatewaySettings(NexusSettings):
    """Gateway process configuration."""

    service_name: Literal["gateway"] = "gateway"
    users_service_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8001")
    orders_service_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8002")
    request_timeout_seconds: float = Field(default=5.0, gt=0)


class UsersSettings(NexusSettings):
    """Users service process configuration."""

    service_name: Literal["users"] = "users"


def _missing_database_url() -> str:
    raise ValueError("NEXUS_DATABASE_URL is required for the Orders service")


class OrdersSettings(NexusSettings):
    """Orders service process configuration."""

    service_name: Literal["orders"] = "orders"
    database_url: str = Field(default_factory=_missing_database_url, min_length=1)


def load_settings() -> NexusSettings:
    """Load NEXUS settings from environment and local .env overrides."""

    return NexusSettings()
