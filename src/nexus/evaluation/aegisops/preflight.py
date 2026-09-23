"""Secret-safe preflight gate that prevents invalid paid benchmark execution."""

import os
from enum import StrEnum

import httpx
from pydantic import BaseModel, ConfigDict

from nexus.aegisops.config import AgentSettings
from nexus.aegisops.tools import build_sdk_tools
from nexus.diagnostics.config import DiagnosticsSettings
from nexus.evaluation.aegisops.benchmark_models import BaselineIdentity
from nexus.evaluation.aegisops.identity import build_baseline_identity, repository_state
from nexus.lab.catalog import ScenarioCatalog


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class PreflightCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: CheckStatus
    detail: str


class PreflightReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool
    checks: list[PreflightCheck]
    identity: BaselineIdentity | None = None


class BenchmarkPreflight:
    def __init__(
        self,
        agent_settings: AgentSettings,
        diagnostics_settings: DiagnosticsSettings | None = None,
        catalog: ScenarioCatalog | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.agent_settings = agent_settings
        self.diagnostics_settings = diagnostics_settings or DiagnosticsSettings()
        self.catalog = catalog or ScenarioCatalog.load()
        self.transport = transport

    def run(self, *, allow_dirty: bool = False) -> PreflightReport:
        checks: list[PreflightCheck] = []
        self._record(
            checks,
            "live_agent_enabled",
            self.agent_settings.enabled,
            "enabled" if self.agent_settings.enabled else "set NEXUS_AGENT_ENABLED=true",
        )
        self._record(
            checks,
            "openai_api_key",
            bool(os.getenv("OPENAI_API_KEY")),
            "present" if os.getenv("OPENAI_API_KEY") else "missing",
        )
        self._record(
            checks,
            "model_identifier",
            bool(self.agent_settings.model.strip()),
            self.agent_settings.model or "missing",
        )
        try:
            scenarios = self.catalog.list()
            self._record(checks, "scenario_catalog", len(scenarios) > 0, f"{len(scenarios)} valid")
        except Exception:
            self._record(checks, "scenario_catalog", False, "catalog validation failed")
        try:
            tools = build_sdk_tools()
            self._record(checks, "tool_policy_boundary", len(tools) == 11, "11 read-only tools")
        except Exception:
            self._record(checks, "tool_policy_boundary", False, "boundary validation failed")

        identity: BaselineIdentity | None = None
        try:
            sha, dirty = repository_state()
            self._record(checks, "repository_sha", bool(sha), sha)
            clean_enough = not dirty or allow_dirty
            detail = "clean" if not dirty else "dirty override" if allow_dirty else "dirty"
            self._record(checks, "working_tree", clean_enough, detail)
            if clean_enough:
                identity = build_baseline_identity(
                    self.agent_settings,
                    self.catalog,
                    allow_dirty=allow_dirty,
                )
        except ValueError as exc:
            self._record(checks, "repository_sha", False, str(exc))

        with httpx.Client(timeout=5, transport=self.transport) as client:
            targets = (
                ("gateway_health", self.diagnostics_settings.gateway_url, "/health"),
                ("users_health", self.diagnostics_settings.users_url, "/health"),
                ("orders_health", self.diagnostics_settings.orders_url, "/health"),
                ("prometheus", self.diagnostics_settings.prometheus_url, "/-/ready"),
                ("loki", self.diagnostics_settings.loki_url, "/ready"),
                ("tempo", self.diagnostics_settings.tempo_url, "/ready"),
            )
            for name, url, path in targets:
                self._http_check(checks, client, name, url, path)
            for name, url in (
                ("users_lab_control", self.diagnostics_settings.users_url),
                ("orders_lab_control", self.diagnostics_settings.orders_url),
            ):
                self._http_check(checks, client, name, url, "/__lab/failures/state")
        return PreflightReport(
            ready=all(check.status is CheckStatus.PASSED for check in checks),
            checks=checks,
            identity=identity,
        )

    @staticmethod
    def _record(checks: list[PreflightCheck], name: str, passed: bool, detail: str) -> None:
        checks.append(
            PreflightCheck(
                name=name,
                status=CheckStatus.PASSED if passed else CheckStatus.FAILED,
                detail=detail,
            )
        )

    @classmethod
    def _http_check(
        cls,
        checks: list[PreflightCheck],
        client: httpx.Client,
        name: str,
        base_url: object,
        path: str,
    ) -> None:
        try:
            response = client.get(f"{str(base_url).rstrip('/')}{path}")
            cls._record(checks, name, response.status_code == 200, f"HTTP {response.status_code}")
        except httpx.HTTPError:
            cls._record(checks, name, False, "unreachable")
