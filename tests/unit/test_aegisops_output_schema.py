import json
from uuid import uuid4

import pytest
from agents import Agent
from agents.exceptions import ModelBehaviorError
from agents.models.openai_responses import Converter
from agents.run_internal.turn_preparation import get_output_schema

from nexus.aegisops.models import Diagnosis, DiagnosisStatus
from nexus.aegisops.output_schema import DIAGNOSIS_OUTPUT_SCHEMA


def _assert_closed_objects(value: object) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
            assert set(value.get("required", [])) == set(value.get("properties", {}))
        for child in value.values():
            _assert_closed_objects(child)
    elif isinstance(value, list):
        for child in value:
            _assert_closed_objects(child)


def _assert_no_empty_schema(value: object) -> None:
    if isinstance(value, dict):
        assert value
        for child in value.values():
            _assert_no_empty_schema(child)


def _assert_no_unsupported_composition(value: object) -> None:
    if isinstance(value, dict):
        assert not ({"allOf", "oneOf", "not", "if", "then", "else"} & set(value))
        for child in value.values():
            _assert_no_unsupported_composition(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_unsupported_composition(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_empty_schema(child)


def test_strict_output_schema_is_a_closed_required_root_object() -> None:
    schema = DIAGNOSIS_OUTPUT_SCHEMA.json_schema()
    assert schema["type"] == "object"
    assert "anyOf" not in schema
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["$defs"]["JsonValue"] != {}
    _assert_closed_objects(schema)
    _assert_no_empty_schema(schema)
    _assert_no_unsupported_composition(schema)


def test_exact_agents_request_schema_uses_the_adapter_without_tools() -> None:
    agent = Agent(
        name="schema-test",
        instructions="Return the requested structured result.",
        tools=[],
        output_type=DIAGNOSIS_OUTPUT_SCHEMA,
    )
    resolved = get_output_schema(agent)
    assert resolved is DIAGNOSIS_OUTPUT_SCHEMA
    response_format = Converter.get_response_format(resolved)
    assert response_format["format"]["strict"] is True
    assert response_format["format"]["schema"]["type"] == "object"
    assert response_format["format"]["schema"]["$defs"]["JsonValue"] != {}


def test_output_schema_parses_diagnosed_result_and_compound_evidence() -> None:
    payload = {
        "status": "diagnosed",
        "summary": "Users is unavailable.",
        "primary_hypothesis": {
            "component": "users",
            "failure_class": "service_unavailable",
            "rationale": "Health and request evidence agree.",
            "confidence": 0.9,
        },
        "supporting_evidence": [
            {
                "tool_call_id": str(uuid4()),
                "tool": "get_service_health",
                "result_path": "health.dependencies",
                "observed_value": {"serialized_object": '{"postgres":"healthy"}'},
                "observation": "The dependency map reports PostgreSQL healthy.",
            }
        ],
        "conflicting_evidence": [],
        "alternatives": [],
        "confidence": 0.9,
        "next_diagnostic_action": "Read correlation-scoped request evidence.",
    }
    diagnosis = DIAGNOSIS_OUTPUT_SCHEMA.validate_json(json.dumps(payload))
    assert isinstance(diagnosis, Diagnosis)
    assert diagnosis.status is DiagnosisStatus.DIAGNOSED
    assert diagnosis.supporting_evidence[0].observed_value == {"postgres": "healthy"}


def test_output_schema_parses_insufficient_evidence_result() -> None:
    payload = {
        "status": "insufficient_evidence",
        "summary": "No diagnostic evidence was provided.",
        "primary_hypothesis": None,
        "supporting_evidence": [],
        "conflicting_evidence": [],
        "alternatives": [],
        "confidence": 0,
        "next_diagnostic_action": "Read system health.",
    }
    diagnosis = DIAGNOSIS_OUTPUT_SCHEMA.validate_json(json.dumps(payload))
    assert diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert diagnosis.primary_hypothesis is None


def test_output_schema_rejects_malformed_output() -> None:
    with pytest.raises(ModelBehaviorError, match="invalid structured Diagnosis"):
        DIAGNOSIS_OUTPUT_SCHEMA.validate_json('{"status":"diagnosed"}')

    malformed_object = {
        "status": "insufficient_evidence",
        "summary": "Malformed evidence value.",
        "primary_hypothesis": None,
        "supporting_evidence": [
            {
                "tool_call_id": str(uuid4()),
                "tool": "get_system_health",
                "result_path": "services.0",
                "observed_value": {"serialized_object": "[]"},
                "observation": "The path resolves to an object.",
            }
        ],
        "conflicting_evidence": [],
        "alternatives": [],
        "confidence": 0,
        "next_diagnostic_action": "Read system health again.",
    }
    with pytest.raises(ModelBehaviorError, match="invalid structured Diagnosis"):
        DIAGNOSIS_OUTPUT_SCHEMA.validate_json(json.dumps(malformed_object))
