"""Typed, operator-owned configuration for the AegisOps investigator."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Bounded execution settings loaded without exposing environment data to the agent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NEXUS_AGENT_",
        extra="ignore",
    )

    enabled: bool = False
    model: str = Field(default="gpt-5-mini", min_length=1, max_length=100)
    max_turns: int = Field(default=10, ge=1, le=30)
    timeout_seconds: float = Field(default=120, gt=0, le=600)
    max_tool_calls: int = Field(default=12, ge=1, le=50)
    sdk_tracing_enabled: bool = False
