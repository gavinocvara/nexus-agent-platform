"""Scenario-isolated evaluator for the single AegisOps investigator."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx

from nexus import __version__
from nexus.aegisops.config import AgentSettings
from nexus.aegisops.instructions import instruction_hash
from nexus.aegisops.runtime import GENERIC_INCIDENT_PROMPT, InvestigatorEngine, InvestigatorRuntime
from nexus.aegisops.tools import tool_registry_hash
from nexus.brain.config import BrainSettings
from nexus.brain.models import BrainMode
from nexus.diagnostics.audit import DiagnosticSession
from nexus.diagnostics.service import DiagnosticServiceLayer
from nexus.evaluation.aegisops.models import (
    EvaluationMetadata,
    EvaluationReport,
    EvaluationRun,
)
from nexus.evaluation.aegisops.scoring import aggregate, score_scenario
from nexus.lab.catalog import ScenarioCatalog
from nexus.lab.runner import ScenarioRunner, default_service_urls
from nexus.lab.schema import ScenarioService


class AegisOpsEvaluationHarness:
    """Activate ground truth outside agent context and guarantee reset and recovery."""

    def __init__(
        self,
        settings: AgentSettings,
        engine: InvestigatorEngine | None = None,
        catalog: ScenarioCatalog | None = None,
        scenario_runner: ScenarioRunner | None = None,
        brain_settings: BrainSettings | None = None,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.brain_settings = brain_settings or BrainSettings(mode=BrainMode.DISABLED)
        if self.brain_settings.mode is BrainMode.LEARN:
            raise ValueError(
                "AegisOpsEvaluationHarness refuses writable Brain mode; "
                "use the guarded benchmark targeted command for calibration"
            )
        self.catalog = catalog or ScenarioCatalog.load()
        self.scenario_runner = scenario_runner or ScenarioRunner(
            self.catalog, default_service_urls()
        )

    async def run(self, runs_per_scenario: int = 1) -> EvaluationReport:
        if runs_per_scenario < 1:
            raise ValueError("runs_per_scenario must be at least 1")
        runs: list[EvaluationRun] = []
        with httpx.Client(timeout=15) as client:
            for scenario in self.catalog.list():
                health_service = ScenarioService(scenario.recovery.health_service.value)
                for repetition in range(1, runs_per_scenario + 1):
                    self.scenario_runner.reset(scenario, client)
                    self.scenario_runner.verify_health(health_service, 200, client)
                    try:
                        self.scenario_runner.activate(scenario, client)
                        for expectation in scenario.expected_symptoms:
                            self.scenario_runner.observe(expectation, client)
                        session = DiagnosticSession(max_tool_calls=self.settings.max_tool_calls)
                        with DiagnosticServiceLayer(session=session) as diagnostics:
                            runtime = InvestigatorRuntime(
                                self.settings,
                                self.engine,
                                brain_settings=self.brain_settings,
                            )
                            record = await runtime.investigate(
                                GENERIC_INCIDENT_PROMPT, diagnostics=diagnostics
                            )
                        score = score_scenario(scenario, repetition, record, session)
                        runs.append(EvaluationRun(record=record, score=score))
                    finally:
                        self.scenario_runner.reset(scenario, client)
                        self.scenario_runner.verify_health(
                            health_service, scenario.recovery.expected_status, client
                        )
        metadata = EvaluationMetadata(
            created_at=datetime.now(UTC),
            git_sha=_git_sha(),
            nexus_version=__version__,
            model=self.settings.model,
            instruction_hash=instruction_hash(),
            tool_registry_hash=tool_registry_hash(),
            scenario_schema_version=1,
            runs_per_scenario=runs_per_scenario,
            max_turns=self.settings.max_turns,
            timeout_seconds=self.settings.timeout_seconds,
            max_tool_calls=self.settings.max_tool_calls,
            sdk_tracing_enabled=self.settings.sdk_tracing_enabled,
        )
        return EvaluationReport(
            metadata=metadata,
            aggregate=aggregate([item.score for item in runs]),
            runs=runs,
        )

    @staticmethod
    def save(report: EvaluationReport, directory: Path | None = None) -> Path:
        target = directory or Path(".nexus/evaluations/aegisops")
        target.mkdir(parents=True, exist_ok=True)
        timestamp = report.metadata.created_at.strftime("%Y%m%dT%H%M%SZ")
        path = target / f"evaluation-{timestamp}.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return path


def _git_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"
