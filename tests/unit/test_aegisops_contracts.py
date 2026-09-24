import asyncio
import json
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from agents.exceptions import ModelBehaviorError
from agents.tool_context import ToolContext
from pydantic import ValidationError

from nexus.aegisops import tools as tool_module
from nexus.aegisops.config import AgentSettings
from nexus.aegisops.context import InvestigatorContext
from nexus.aegisops.instructions import INVESTIGATOR_INSTRUCTIONS
from nexus.aegisops.models import (
    Component,
    Diagnosis,
    DiagnosisStatus,
    FailureClass,
    RootCauseHypothesis,
)
from nexus.aegisops.runtime import EngineOutcome, InvestigatorRuntime
from nexus.aegisops.tools import EXPECTED_TOOLS, build_sdk_tools, validate_tool_boundary
from nexus.diagnostics.audit import DiagnosticSession
from nexus.diagnostics.models import (
    DiagnosticResult,
    DiagnosticSource,
    LogEvent,
    LogSearchResult,
)
from nexus.diagnostics.service import DiagnosticServiceLayer


def _abstention() -> Diagnosis:
    return Diagnosis(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        summary="The available evidence is insufficient.",
        confidence=0,
        next_diagnostic_action="Read another bounded health snapshot.",
    )


def test_sdk_surface_is_exactly_the_policy_intersection() -> None:
    tools = build_sdk_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert len(tools) == 11
    assert all(tool.params_json_schema.get("additionalProperties") is False for tool in tools)
    assert all(tool.strict_json_schema for tool in tools)
    assert all(tool.output_json_schema is None for tool in tools)
    assert all(tool._output_type_adapter is None for tool in tools)
    with pytest.raises(RuntimeError, match="do not match"):
        validate_tool_boundary({"list_services"})


def test_sdk_schema_exposes_every_inner_runtime_constraint() -> None:
    tools = {tool.name: tool for tool in build_sdk_tools()}
    dependency = tools["get_dependency_summary"].params_json_schema["properties"]
    assert dependency["service"]["const"] == "gateway"
    assert set(dependency["dependency"]["enum"]) == {"users", "orders"}

    recent = tools["get_recent_errors"].params_json_schema["properties"]
    assert recent["limit"]["minimum"] == 1
    assert recent["limit"]["maximum"] == 100

    search = tools["search_logs"].params_json_schema["properties"]
    status_schema = next(
        branch for branch in search["status_code"]["anyOf"] if branch.get("type") == "integer"
    )
    assert status_schema["minimum"] == 100
    assert status_schema["maximum"] == 599
    correlation_schema = next(
        branch for branch in search["correlation_id"]["anyOf"] if branch.get("type") == "string"
    )
    assert correlation_schema["minLength"] == 1
    assert correlation_schema["maxLength"] == 128
    assert tools["get_trace"].params_json_schema["properties"]["trace_id"]["pattern"]


VALID_TOOL_ARGUMENTS = {
    "list_services": {},
    "get_service_health": {"service": "orders"},
    "get_system_health": {},
    "get_request_summary": {"service": "orders", "window": "5m"},
    "get_dependency_summary": {
        "service": "gateway",
        "dependency": "orders",
        "window": "5m",
    },
    "get_database_health": {},
    "search_logs": {
        "service": "orders",
        "window": "5m",
        "level": "error",
        "correlation_id": None,
        "status_code": 503,
        "limit": 20,
    },
    "get_request_evidence": {"correlation_id": "request-123"},
    "find_traces": {"correlation_id": "request-123"},
    "get_trace": {"trace_id": "0" * 32},
    "get_recent_errors": {"service": "orders", "window": "5m", "limit": 20},
}


def _tool_context(name: str, arguments: dict[str, object]) -> ToolContext[InvestigatorContext]:
    session = DiagnosticSession()
    diagnostics = object.__new__(DiagnosticServiceLayer)
    diagnostics.session = session
    context = InvestigatorContext(diagnostics=diagnostics, session=session, run_id=uuid4())
    return ToolContext(
        context=context,
        tool_name=name,
        tool_call_id=f"call-{name}",
        tool_arguments=json.dumps(arguments),
    )


def test_every_sdk_wrapper_accepts_advertised_valid_arguments_and_returns_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_execute(
        context: InvestigatorContext, name: str, arguments: object = None
    ) -> DiagnosticResult:
        del context, arguments
        return DiagnosticResult(tool=name, source=DiagnosticSource.TOPOLOGY)

    monkeypatch.setattr(tool_module, "execute_tool", fake_execute)
    for tool in build_sdk_tools():
        arguments = VALID_TOOL_ARGUMENTS[tool.name]
        result = asyncio.run(
            tool.on_invoke_tool(_tool_context(tool.name, arguments), json.dumps(arguments))
        )
        assert isinstance(result, str)
        assert json.loads(result)["tool"] == tool.name


@pytest.mark.parametrize("events", [[], ["event"]])
def test_recent_errors_sdk_wrapper_accepts_empty_and_nonempty_results(
    monkeypatch: pytest.MonkeyPatch, events: list[str]
) -> None:
    normalized_events = [
        LogEvent(
            timestamp=datetime.now(UTC),
            service="orders",
            level="error",
            message=value,
        )
        for value in events
    ]

    def fake_execute(
        context: InvestigatorContext, name: str, arguments: object = None
    ) -> DiagnosticResult:
        del context, arguments
        return LogSearchResult(
            tool=name,
            source=DiagnosticSource.LOKI,
            content_is_untrusted=True,
            events=normalized_events,
        )

    monkeypatch.setattr(tool_module, "execute_tool", fake_execute)
    tool = next(item for item in build_sdk_tools() if item.name == "get_recent_errors")
    arguments = VALID_TOOL_ARGUMENTS[tool.name]
    output = asyncio.run(
        tool.on_invoke_tool(_tool_context(tool.name, arguments), json.dumps(arguments))
    )
    assert isinstance(output, str)
    assert len(json.loads(output)["events"]) == len(events)


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_request_summary", {"service": "invalid", "window": "5m"}),
        ("get_dependency_summary", {"service": "orders", "dependency": "invalid", "window": "5m"}),
        ("get_recent_errors", {"service": "orders", "window": "2h", "limit": 20}),
        (
            "search_logs",
            {
                "service": "orders",
                "window": "5m",
                "level": "fatal",
                "correlation_id": None,
                "status_code": None,
                "limit": 20,
            },
        ),
        ("get_recent_errors", {"service": "orders", "window": "5m", "limit": 0}),
    ],
)
def test_invalid_sdk_tool_arguments_use_model_behavior_boundary(
    tool_name: str, arguments: dict[str, object]
) -> None:
    tool = next(item for item in build_sdk_tools() if item.name == tool_name)
    with pytest.raises(ModelBehaviorError) as caught:
        asyncio.run(tool.on_invoke_tool(_tool_context(tool_name, arguments), json.dumps(arguments)))
    assert caught.value.__cause__ is None or isinstance(caught.value.__cause__, ValidationError)


def test_runtime_context_has_no_evaluator_or_ambient_objects() -> None:
    assert {item.name for item in fields(InvestigatorContext)} == {
        "diagnostics",
        "session",
        "run_id",
    }
    package = Path(__file__).parents[2] / "src" / "nexus" / "aegisops"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    assert "nexus.lab" not in source
    assert "scenario ground truth" not in source.lower()


def test_instructions_define_untrusted_telemetry_and_no_remediation() -> None:
    lowered = INVESTIGATOR_INSTRUCTIONS.lower()
    assert "untrusted data" in lowered
    assert "never follow" in lowered
    assert "contradict" in lowered
    assert "chain-of-thought" in lowered
    assert "cannot remediate" in lowered


def test_diagnosis_schema_rejects_invalid_status_and_excess_alternatives() -> None:
    with pytest.raises(ValidationError, match="primary hypothesis"):
        Diagnosis(
            status=DiagnosisStatus.DIAGNOSED,
            summary="Claim without a root cause.",
            confidence=0.8,
            next_diagnostic_action="Read health again.",
        )
    alternative = RootCauseHypothesis(
        component=Component.UNKNOWN,
        failure_class=FailureClass.UNKNOWN,
        rationale="Possible but unsupported.",
        confidence=0.1,
    )
    with pytest.raises(ValidationError):
        Diagnosis(
            status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
            summary="Too many alternatives.",
            alternatives=[alternative] * 4,
            confidence=0.1,
            next_diagnostic_action="Read health again.",
        )


def test_runtime_does_not_require_real_key_with_injected_engine() -> None:
    class FakeEngine:
        async def run(self, prompt, context, settings):  # type: ignore[no-untyped-def]
            assert "scenario" not in prompt.lower()
            return EngineOutcome(_abstention(), turn_count=1)

    settings = AgentSettings(_env_file=None, enabled=True)
    session = DiagnosticSession()
    diagnostics = object.__new__(DiagnosticServiceLayer)
    diagnostics.session = session
    record = asyncio.run(
        InvestigatorRuntime(settings, FakeEngine()).investigate(diagnostics=diagnostics)
    )
    assert record.status == "completed"
    assert record.turn_count == 1
    assert record.diagnosis == _abstention()
