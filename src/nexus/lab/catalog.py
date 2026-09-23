"""Load and validate evaluator ground truth from versioned JSON files."""

import os
from pathlib import Path

from pydantic import ValidationError

from nexus.lab.schema import FailureScenario


class ScenarioCatalogError(ValueError):
    """Raised when scenario storage is missing or malformed."""


class ScenarioCatalog:
    """Immutable-by-convention collection of validated scenarios."""

    def __init__(self, scenarios: tuple[FailureScenario, ...]) -> None:
        self._scenarios = {scenario.id: scenario for scenario in scenarios}
        if len(self._scenarios) != len(scenarios):
            raise ScenarioCatalogError("Scenario IDs must be unique")

    @classmethod
    def load(cls, directory: Path | None = None) -> "ScenarioCatalog":
        """Load every JSON scenario from the configured directory."""

        scenario_directory = directory or Path(
            os.getenv("NEXUS_SCENARIO_DIRECTORY", "lab/scenarios/v1")
        )
        files = sorted(scenario_directory.glob("*.json"))
        if not files:
            raise ScenarioCatalogError(f"No scenarios found in {scenario_directory}")

        scenarios: list[FailureScenario] = []
        for path in files:
            try:
                scenario = FailureScenario.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError, ValueError) as exc:
                raise ScenarioCatalogError(f"Invalid scenario {path}: {exc}") from exc
            if path.stem != scenario.id:
                raise ScenarioCatalogError(
                    f"Scenario filename {path.stem!r} does not match ID {scenario.id!r}"
                )
            scenarios.append(scenario)
        return cls(tuple(scenarios))

    def list(self) -> tuple[FailureScenario, ...]:
        """Return scenarios ordered by stable ID."""

        return tuple(self._scenarios[key] for key in sorted(self._scenarios))

    def get(self, scenario_id: str) -> FailureScenario:
        """Return one known scenario or fail clearly."""

        try:
            return self._scenarios[scenario_id]
        except KeyError as exc:
            raise ScenarioCatalogError(f"Unknown scenario: {scenario_id}") from exc
