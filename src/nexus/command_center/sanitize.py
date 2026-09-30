"""The one place text crosses from NEXUS records into a browser payload."""

from __future__ import annotations

from nexus.software_engineer.trust import contains_credential

WITHHELD = "[withheld: credential-shaped]"


def safe_text(value: str, limit: int = 500) -> str:
    """Bound ``value`` and withhold it entirely when it looks like a credential.

    Withholding the whole string, rather than masking a match, means a partial pattern can
    never leak the rest of a secret.
    """

    if contains_credential(value):
        return WITHHELD
    text = value.replace("\x00", "")
    if len(text) > limit:
        return text[: max(limit - 1, 0)] + "…"
    return text


def safe_optional(value: str | None, limit: int = 500) -> str | None:
    return None if value is None else safe_text(value, limit)


def safe_list(values: list[str], limit: int = 500, count: int = 50) -> list[str]:
    return [safe_text(item, limit) for item in values[:count]]


__all__ = ["WITHHELD", "safe_list", "safe_optional", "safe_text"]
