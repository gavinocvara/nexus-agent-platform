"""Owner decisions through Slack slash commands, verified before they mean anything.

A Slack message is untrusted text until three checks pass: the request carries a valid
``v0`` HMAC signature computed with the signing secret that lives only in the environment,
its timestamp is inside the replay window, and the Slack user id is the one configured as
the owner. Only then does ``/nexus ship|revise|reject [request|latest] <reason>`` become a
typed ``OwnerCommand`` over the ``slack`` channel and a recorded ``OwnerDecision``, once per
request, exactly as the ``decide`` CLI records it. Nothing here publishes: SHIP still needs
the owner-run ``publish`` step (or the opted-in autonomous path).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, Response

from nexus.software_engineer.approval import ApprovalError, decide
from nexus.software_engineer.memory import EngineerMemoryStore
from nexus.software_engineer.models import OwnerDecision, OwnerVerdict
from nexus.software_engineer.trust import contains_credential

SLACK_SIGNING_SECRET_ENV_DEFAULT = "NEXUS_SOFTWARE_ENGINEER_SLACK_SIGNING_SECRET"
DEFAULT_REPLAY_WINDOW_SECONDS = 300
MAX_BODY_BYTES = 20_000
MAX_REASON_CHARS = 500
_VERDICTS = {item.value: item for item in OwnerVerdict}


class SlackCommandError(RuntimeError):
    """A command was refused; ``code`` is stable and ``status`` is the HTTP status."""

    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    verdict: OwnerVerdict
    cycle: str
    reason: str
    user_id: str
    team_id: str
    channel_id: str


@dataclass(frozen=True, slots=True)
class SlackReply:
    status: int
    text: str
    decision: OwnerDecision | None = None

    def payload(self) -> dict[str, str]:
        return {"response_type": "ephemeral", "text": self.text}


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: datetime,
    window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS,
) -> None:
    """Slack's ``v0`` scheme: ``v0=`` + HMAC-SHA256(secret, ``v0:<timestamp>:<body>``)."""

    if not timestamp or not signature:
        raise SlackCommandError("signature_missing", 401)
    if not timestamp.isdigit():
        raise SlackCommandError("timestamp_invalid", 401)
    sent = int(timestamp)
    if abs(int(now.timestamp()) - sent) > window_seconds:
        raise SlackCommandError("timestamp_stale", 401)
    if len(body) > MAX_BODY_BYTES:
        raise SlackCommandError("body_too_large", 413)
    base = b"v0:" + timestamp.encode("ascii") + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip()):
        raise SlackCommandError("signature_invalid", 401)


def parse_slash_command(body: bytes) -> ParsedCommand:
    """``<ship|revise|reject> [<cycle-id>|latest] <reason>`` from Slack's form body."""

    form = {
        key: values[0]
        for key, values in parse_qs(body.decode("utf-8", errors="strict")).items()
        if values
    }
    user_id = form.get("user_id", "").strip()
    if not user_id:
        raise SlackCommandError("user_missing")
    words = form.get("text", "").split()
    if not words or words[0].casefold() not in _VERDICTS:
        raise SlackCommandError("command_unparsable")
    verdict = _VERDICTS[words[0].casefold()]
    rest = words[1:]
    cycle = "latest"
    if rest and (rest[0].casefold() == "latest" or _looks_like_uuid(rest[0])):
        cycle = rest[0].casefold() if rest[0].casefold() == "latest" else rest[0]
        rest = rest[1:]
    reason = " ".join(rest).strip()
    if not reason:
        raise SlackCommandError("reason_required")
    return ParsedCommand(
        verdict=verdict,
        cycle=cycle,
        reason=reason[:MAX_REASON_CHARS],
        user_id=user_id,
        team_id=form.get("team_id", "").strip(),
        channel_id=form.get("channel_id", "").strip(),
    )


def _looks_like_uuid(value: str) -> bool:
    parts = value.split("-")
    return (
        len(value) == 36
        and [len(part) for part in parts] == [8, 4, 4, 4, 12]
        and all(all(c in "0123456789abcdefABCDEF" for c in part) for part in parts)
    )


class SlackCommandHandler:
    """Verify, authorize, and record. The signing secret is read per request, never kept."""

    def __init__(
        self,
        *,
        state_root: Path,
        memory: EngineerMemoryStore,
        owner_id: str,
        slack_owner_user_id: str | None,
        signing_secret_env: str = SLACK_SIGNING_SECRET_ENV_DEFAULT,
        clock: Callable[[], datetime] | None = None,
        window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS,
    ) -> None:
        self.state_root = state_root
        self.memory = memory
        self.owner_id = owner_id
        self.slack_owner_user_id = (slack_owner_user_id or "").strip() or None
        self.signing_secret_env = signing_secret_env
        self.clock = clock or (lambda: datetime.now(UTC))
        self.window_seconds = window_seconds

    def handle(self, headers: Mapping[str, str], body: bytes) -> SlackReply:
        now = self.clock()
        try:
            self._verify(headers, body, now)
            command = parse_slash_command(body)
            if self.slack_owner_user_id is None or command.user_id != self.slack_owner_user_id:
                raise SlackCommandError("not_owner", 200)
            decision = decide(
                state_root=self.state_root,
                memory=self.memory,
                owner_id=self.owner_id,
                cycle=command.cycle,
                verdict=command.verdict,
                reason=command.reason,
                now=now,
                channel="slack",
            )
        except SlackCommandError as exc:
            return SlackReply(exc.status, f"Refused: {exc.code}.")
        except ApprovalError as exc:
            return SlackReply(200, f"Refused: {exc.code}.")
        except UnicodeDecodeError:
            return SlackReply(400, "Refused: body_unreadable.")
        text = (
            f"Recorded {decision.verdict.value.upper()} for request {decision.request_id} "
            f"(decision {decision.decision_id}). "
        )
        if decision.verdict is OwnerVerdict.SHIP:
            text += "Nothing is published yet: run `python -m nexus.software_engineer publish`."
        else:
            text += "The engineer will not publish this change."
        if contains_credential(text):
            return SlackReply(200, "Recorded; details withheld because they looked like a secret.")
        return SlackReply(200, text, decision)

    def _verify(self, headers: Mapping[str, str], body: bytes, now: datetime) -> None:
        lowered = {key.lower(): value for key, value in headers.items()}
        secret = os.environ.get(self.signing_secret_env, "").strip()
        if not secret:
            raise SlackCommandError("credentials_missing", 503)
        try:
            verify_slack_signature(
                signing_secret=secret,
                timestamp=lowered.get("x-slack-request-timestamp"),
                signature=lowered.get("x-slack-signature"),
                body=body,
                now=now,
                window_seconds=self.window_seconds,
            )
        finally:
            del secret


def create_app(handler: SlackCommandHandler) -> FastAPI:
    """A minimal FastAPI app: ``POST /slack/commands`` and ``GET /healthz``."""

    app = FastAPI(title="NEXUS resident engineer owner commands", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/slack/commands")
    async def slack_commands(request: Request) -> Response:
        body = await request.body()
        reply = handler.handle(dict(request.headers), body)
        return Response(
            content=json.dumps(reply.payload()),
            status_code=reply.status,
            media_type="application/json",
        )

    return app


__all__ = [
    "DEFAULT_REPLAY_WINDOW_SECONDS",
    "SLACK_SIGNING_SECRET_ENV_DEFAULT",
    "ParsedCommand",
    "SlackCommandError",
    "SlackCommandHandler",
    "SlackReply",
    "create_app",
    "parse_slash_command",
    "verify_slack_signature",
]
