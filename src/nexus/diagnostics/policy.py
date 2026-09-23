"""Explicit precursor policy for the future AegisOps investigator."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class DeniedCapability(StrEnum):
    """Capabilities intentionally absent from the diagnostic runtime."""

    LAB_CONTROLS = "lab_controls"
    SCENARIO_GROUND_TRUTH = "scenario_ground_truth"
    FILESYSTEM = "filesystem"
    SHELL = "shell"
    GITHUB = "github"
    DATABASE_SQL = "database_sql"
    DOCKER = "docker"
    WRITE_ACTIONS = "write_actions"
    ARBITRARY_URLS = "arbitrary_urls"
    RAW_PROMQL = "raw_promql"
    RAW_LOGQL = "raw_logql"
    RAW_TRACE_SEARCH = "raw_trace_search"
    GRAFANA = "grafana"
    ENVIRONMENT_SECRETS = "environment_secrets"


class InvestigatorPolicy(BaseModel):
    """Read-only capability declaration designed for later Atlas enforcement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    allowed_tools: tuple[str, ...]
    denied_capabilities: tuple[DeniedCapability, ...]
    read_only: bool


INVESTIGATOR_POLICY = InvestigatorPolicy(
    role="aegisops.investigator",
    allowed_tools=(
        "list_services",
        "get_service_health",
        "get_system_health",
        "get_request_summary",
        "get_dependency_summary",
        "get_database_health",
        "search_logs",
        "get_request_evidence",
        "get_trace",
        "find_traces",
        "get_recent_errors",
    ),
    denied_capabilities=tuple(DeniedCapability),
    read_only=True,
)
