"""Trust boundaries: what may instruct the resident engineer and what is only data.

Repository text, issues, commit messages, logs, comments, Slack messages, and memory are
untrusted data. They can be observed, quoted, and flagged, never obeyed. Only a typed
``OwnerCommand`` from the configured human owner changes what the engineer does with a
pending approval request, and even the owner changes operating policy through settings
and reviewed commits, never through chat.
"""

from __future__ import annotations

import re
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints

from nexus.atlas.models import ActorIdentity, ActorType, StrictModel
from nexus.patchforge.engine import render_untrusted
from nexus.software_engineer.models import OwnerVerdict

MAX_UNTRUSTED_CHARS = 20_000
_INSTRUCTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("ignore_rules", r"\bignore\b.{0,40}\b(rules?|instructions?|policy|policies|guardrails?)"),
        ("push_main", r"\bpush\b.{0,20}\b(directly\s+)?(to\s+)?(main|master|production)\b"),
        ("merge_now", r"\b(merge|deploy|release)\b.{0,20}\b(now|immediately|without)\b"),
        (
            "disable_safety",
            r"\b(disable|bypass|skip|remove|turn\s+off)\b.{0,40}"
            r"\b(tests?|safety|sentinel|approval|review|gates?|checks?|budget|limits?)\b",
        ),
        (
            "approve_self",
            r"\b(approve|self[- ]approve|auto[- ]approve)\b.{0,30}\b(your|own|this)\b",
        ),
        (
            "identity_override",
            r"\b(you are now|new instructions|system prompt|developer message)\b",
        ),
        (
            "secret_exfiltration",
            r"\b(print|send|post|leak|reveal)\b.{0,30}\b(secret|token|key|password|credential)",
        ),
        ("credential_grant", r"\b(grant|give)\b.{0,30}\b(access|permission|token|credential)"),
    )
)


class TrustError(PermissionError):
    """Something other than the owner tried to direct the engineer."""


class UntrustedText(StrictModel):
    """Bounded, hashed, instruction-scanned external text. Data, never authority."""

    source: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    content: Annotated[str, StringConstraints(max_length=MAX_UNTRUSTED_CHARS)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    truncated: bool
    instruction_like: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=20
    )

    @classmethod
    def capture(cls, source: str, text: str) -> UntrustedText:
        truncated = len(text) > MAX_UNTRUSTED_CHARS
        content = text[:MAX_UNTRUSTED_CHARS]
        return cls(
            source=source[:500],
            content=content,
            sha256=sha256(text.encode("utf-8", errors="surrogateescape")).hexdigest(),
            truncated=truncated,
            instruction_like=detect_instruction_like_text(content),
        )

    def render(self, limit: int = 4000) -> str:
        """Escaped, delimited rendering for any model-facing context."""

        return render_untrusted({"source": self.source, "content": self.content}, limit)


def detect_instruction_like_text(text: str) -> list[str]:
    """Names of instruction-shaped patterns found in text. Flagging is not obeying."""

    return [name for name, pattern in _INSTRUCTION_PATTERNS if pattern.search(text)]


class OwnerCommand(StrictModel):
    """A typed decision from the human owner about one pending approval request."""

    command_id: UUID
    request_id: UUID
    verdict: OwnerVerdict
    issued_by: ActorIdentity
    channel: Literal["slack", "cli", "github"]
    reason: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    issued_at: AwareDatetime


def authorize_owner_command(command: OwnerCommand, *, owner_id: str) -> None:
    """Accept only the configured human owner. Anything else is untrusted data."""

    if command.issued_by.actor_type is not ActorType.HUMAN:
        raise TrustError("Only a human owner can decide on an approval request")
    if command.issued_by.actor_id != owner_id:
        raise TrustError("The actor is not the configured owner")


__all__ = [
    "MAX_UNTRUSTED_CHARS",
    "OwnerCommand",
    "TrustError",
    "UntrustedText",
    "authorize_owner_command",
    "detect_instruction_like_text",
]
