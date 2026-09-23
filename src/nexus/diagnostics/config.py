"""Operator-owned configuration for fixed diagnostic backends."""

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DiagnosticsSettings(BaseSettings):
    """Predefined backend locations unavailable as investigator tool arguments."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NEXUS_DIAGNOSTICS_",
        extra="ignore",
    )

    gateway_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    users_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8001")
    orders_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8002")
    prometheus_url: AnyHttpUrl = AnyHttpUrl("http://localhost:9090")
    loki_url: AnyHttpUrl = AnyHttpUrl("http://localhost:3100")
    tempo_url: AnyHttpUrl = AnyHttpUrl("http://localhost:3200")
    timeout_seconds: float = Field(default=5.0, gt=0, le=15)
    max_response_bytes: int = Field(default=1_000_000, ge=1024, le=5_000_000)
