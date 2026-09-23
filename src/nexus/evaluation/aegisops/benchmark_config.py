"""Operator configuration for benchmark storage and contamination control."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BenchmarkSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NEXUS_BENCHMARK_",
        extra="ignore",
    )

    root: Path = Path(".nexus/benchmarks/aegisops")
    warmup_seconds: float = Field(default=6, ge=0, le=60)
    stack_timeout_seconds: float = Field(default=180, gt=0, le=600)
