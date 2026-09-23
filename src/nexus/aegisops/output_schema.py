"""Strict Responses API output schema for the AegisOps diagnosis."""

import json
from copy import deepcopy
from typing import Any

from agents import AgentOutputSchema, AgentOutputSchemaBase
from agents.exceptions import ModelBehaviorError
from pydantic import ValidationError

from nexus.aegisops.models import Diagnosis

_SERIALIZED_OBJECT_KEY = "serialized_object"
_JSON_VALUE_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "string"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "null"},
        {
            "type": "array",
            "items": {"$ref": "#/$defs/JsonValue"},
        },
        {
            "type": "object",
            "description": (
                "An exact JSON object value encoded as a JSON string. Use this branch only "
                "when the cited result path resolves to an object."
            ),
            "properties": {
                _SERIALIZED_OBJECT_KEY: {
                    "type": "string",
                    "description": "A JSON-encoded object, including its outer braces.",
                }
            },
            "required": [_SERIALIZED_OBJECT_KEY],
            "additionalProperties": False,
        },
    ]
}


class DiagnosisOutputSchema(AgentOutputSchemaBase):
    """Expose a strict API schema and validate output into the domain Diagnosis model."""

    def __init__(self) -> None:
        schema = deepcopy(AgentOutputSchema(Diagnosis).json_schema())
        definitions = schema.get("$defs")
        if not isinstance(definitions, dict) or "JsonValue" not in definitions:
            raise RuntimeError(
                "Diagnosis schema no longer contains the expected JsonValue definition"
            )
        definitions["JsonValue"] = deepcopy(_JSON_VALUE_SCHEMA)
        self._schema = schema

    def is_plain_text(self) -> bool:
        return False

    def name(self) -> str:
        return "Diagnosis"

    def json_schema(self) -> dict[str, Any]:
        return deepcopy(self._schema)

    def is_strict_json_schema(self) -> bool:
        return True

    def validate_json(self, json_str: str) -> Diagnosis:
        try:
            payload = json.loads(json_str)
            converted = _decode_json_values(payload)
            return Diagnosis.model_validate_json(
                json.dumps(converted, separators=(",", ":")), strict=True
            )
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise ModelBehaviorError("Model returned an invalid structured Diagnosis") from exc


def _decode_json_values(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode_json_values(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {_SERIALIZED_OBJECT_KEY}:
            encoded = value[_SERIALIZED_OBJECT_KEY]
            if not isinstance(encoded, str):
                raise ValueError("serialized_object must be a string")
            decoded = json.loads(encoded)
            if not isinstance(decoded, dict):
                raise ValueError("serialized_object must encode a JSON object")
            return decoded
        return {key: _decode_json_values(item) for key, item in value.items()}
    return value


DIAGNOSIS_OUTPUT_SCHEMA = DiagnosisOutputSchema()
