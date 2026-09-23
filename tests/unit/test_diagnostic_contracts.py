import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from nexus.diagnostics.__main__ import main
from nexus.diagnostics.models import (
    CorrelationInput,
    DependencySummaryInput,
    DiagnosticService,
    DiagnosticWindow,
    LogSearchInput,
    RequestSummaryInput,
    TraceInput,
)
from nexus.diagnostics.policy import INVESTIGATOR_POLICY, DeniedCapability
from nexus.diagnostics.registry import get_tool_registry
from nexus.diagnostics.service import DiagnosticServiceLayer


def test_input_schemas_reject_unknown_services_windows_and_escape_fields() -> None:
    with pytest.raises(ValidationError):
        RequestSummaryInput(service="evaluator", window="5m")
    with pytest.raises(ValidationError):
        RequestSummaryInput(service="orders", window="90d")
    for field in ("promql", "logql", "url", "path", "sql", "shell"):
        with pytest.raises(ValidationError):
            LogSearchInput.model_validate({"service": "orders", field: "attacker-controlled"})


def test_dependency_edges_are_allowlisted() -> None:
    request = DependencySummaryInput(service="gateway", dependency="users", window="1m")
    assert request.window is DiagnosticWindow.ONE_MINUTE
    with pytest.raises(ValidationError):
        DependencySummaryInput(service="orders", dependency="postgres", window="5m")


def test_log_limits_and_identifiers_are_strictly_bounded() -> None:
    assert LogSearchInput(service="gateway").limit == 20
    assert LogSearchInput(service="gateway", limit=100).limit == 100
    with pytest.raises(ValidationError):
        LogSearchInput(service="gateway", limit=101)
    with pytest.raises(ValidationError):
        CorrelationInput(correlation_id='safe"} |~ ".*')
    with pytest.raises(ValidationError):
        CorrelationInput(correlation_id="x" * 129)
    with pytest.raises(ValidationError):
        TraceInput(trace_id="not-a-trace")
    assert TraceInput(trace_id="A" * 32).trace_id == "A" * 32


def test_registry_and_policy_are_complete_and_read_only() -> None:
    registry = get_tool_registry()
    names = tuple(tool.name for tool in registry)
    assert names == INVESTIGATOR_POLICY.allowed_tools
    assert len(names) == len(set(names))
    assert all(tool.read_only and tool.risk == "low" for tool in registry)
    assert INVESTIGATOR_POLICY.read_only is True
    assert set(INVESTIGATOR_POLICY.denied_capabilities) == set(DeniedCapability)


def test_public_service_has_no_raw_query_or_ambient_access_parameters() -> None:
    prohibited = {"promql", "logql", "url", "path", "shell", "sql", "filesystem"}
    for name in INVESTIGATOR_POLICY.allowed_tools:
        parameters = set(inspect.signature(getattr(DiagnosticServiceLayer, name)).parameters)
        assert prohibited.isdisjoint(parameters)


def test_diagnostics_package_has_no_evaluator_import_path() -> None:
    package = Path(__file__).parents[2] / "src" / "nexus" / "diagnostics"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    assert "nexus.lab" not in source
    assert "lab/scenarios" not in source
    assert "/__lab/" not in source


def test_cli_has_no_raw_query_escape_hatch() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["requests", "orders", "--promql", "up"])
    assert exc_info.value.code == 2


def test_inventory_contains_only_operational_components() -> None:
    layer = object.__new__(DiagnosticServiceLayer)
    # list_services has no backend dependency; provide only the session it audits into.
    from nexus.diagnostics.audit import DiagnosticSession

    layer.session = DiagnosticSession()
    result = layer.list_services()
    assert {item.service for item in result.services} == set(DiagnosticService)
    serialized = result.model_dump_json()
    for forbidden in ("failure", "scenario", "evaluator", "lab/scenarios"):
        assert forbidden not in serialized
