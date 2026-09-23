import os

import pytest

from nexus.lab.catalog import ScenarioCatalog
from nexus.lab.runner import ScenarioLifecycle, ScenarioRunner, default_service_urls

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "scenario_id",
    [
        "users_unavailable",
        "users_latency",
        "orders_database_unavailable",
    ],
)
def test_incident_lifecycle(scenario_id: str) -> None:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 with the Compose environment running")

    result = ScenarioRunner(ScenarioCatalog.load(), default_service_urls()).run(scenario_id)

    assert result.lifecycle is ScenarioLifecycle.RECOVERY_VERIFIED
    assert result.scenario_id == scenario_id
    assert result.observations
