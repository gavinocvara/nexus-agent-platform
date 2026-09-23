import asyncio
from dataclasses import fields
from pathlib import Path

import pytest
from pydantic import ValidationError

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
    with pytest.raises(RuntimeError, match="do not match"):
        validate_tool_boundary({"list_services"})


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
