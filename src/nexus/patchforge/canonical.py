"""Canonical serialization and hashing for PatchForge contracts."""

import json
import unicodedata
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from pydantic import BaseModel


def _normalize(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    return value


def canonical_json(value: BaseModel | Mapping[str, Any]) -> str:
    """Return stable UTF-8-safe JSON for a typed record or JSON-compatible mapping."""

    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    return json.dumps(
        _normalize(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def canonical_sha256(value: BaseModel | Mapping[str, Any]) -> str:
    """Hash canonical JSON without relying on process or dictionary ordering."""

    return sha256(canonical_json(value).encode("utf-8")).hexdigest()
