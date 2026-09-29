"""Slack owner commands: a real HMAC, a real replay window, one owner, one decision."""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from nexus.patchforge.e2e import FixtureRepository, materialize_fixture
from nexus.patchforge.workspace import GitRunner
from nexus.software_engineer import __main__ as cli
from nexus.software_engineer.approval import load_cycle_record, load_owner_decision
from nexus.software_engineer.config import SoftwareEngineerSettings
from nexus.software_engineer.cycle import EngineeringCycle
from nexus.software_engineer.inspect import CandidateGenerator, RepositoryInspector
from nexus.software_engineer.memory import EngineerMemory, EngineerMemoryStore, EpistemicStatus
from nexus.software_engineer.models import (
    CandidateEstimate,
    ChangeCategory,
    CycleDecision,
    CycleMode,
    EngineeringCandidate,
    EngineeringSignal,
    OwnerVerdict,
)
from nexus.software_engineer.notify import Notifier, RecordingTransport
from nexus.software_engineer.slack_commands import (
    SlackCommandError,
    SlackCommandHandler,
    create_app,
    parse_slash_command,
    verify_slack_signature,
)

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
SECRET = "slack-signing-secret-test-only-0123456789"
OWNER_SLACK_ID = "U0OWNER001"
CYCLE = UUID(int=980)
FIXTURE = FixtureRepository(
    name="slack-fixture", files={"src/pkg/__init__.py": "", "README.md": "# fixture\n"}
)


class _OnePlan(CandidateGenerator):
    def generate(
        self, signals: Sequence[EngineeringSignal], memories: Sequence[EngineerMemory] = ()
    ) -> list[EngineeringCandidate]:
        return [
            EngineeringCandidate(
                candidate_id=UUID(int=981),
                title="Fix README typo",
                rationale="A typo in the README misleads readers.",
                category=ChangeCategory.DOCUMENTATION_CORRECTION,
                expected_paths=["README.md"],
                signal_ids=[signals[0].signal_id],
                estimate=CandidateEstimate(value=50, urgency=40, confidence=80, cost=10),
            )
        ]


def _state_with_request(tmp_path: Path) -> tuple[Path, EngineerMemoryStore, str]:
    git = GitRunner(tmp_path / "git")
    repo = tmp_path / "repo"
    materialize_fixture(FIXTURE, repo, git, NOW)
    state_root = tmp_path / "state"
    settings = SoftwareEngineerSettings(  # type: ignore[call-arg]
        _env_file=None,
        enabled=True,
        mode=CycleMode.DRY_RUN,
        state_root=state_root,
        memory_path=state_root / "memory.sqlite3",
    )
    memory = EngineerMemoryStore(settings.memory_path)
    record, _ = EngineeringCycle(
        settings=settings,
        inspector=RepositoryInspector(repo, git=git, clock=lambda: NOW),
        memory=memory,
        notifier=Notifier(RecordingTransport(), clock=lambda: NOW),
        generator=_OnePlan(),
        clock=lambda: NOW,
        cycle_id=CYCLE,
    ).run()
    assert record.decision is CycleDecision.REQUEST_APPROVAL
    return state_root, memory, settings.owner_id


def _signed(body: bytes, *, secret: str = SECRET, at: datetime = NOW) -> dict[str, str]:
    timestamp = str(int(at.timestamp()))
    digest = hmac.new(
        secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
    ).hexdigest()
    return {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _body(text: str, user: str = OWNER_SLACK_ID) -> bytes:
    return urlencode(
        {"command": "/nexus", "text": text, "user_id": user, "team_id": "T1", "channel_id": "C1"}
    ).encode()


def _handler(state_root: Path, memory: EngineerMemoryStore, owner_id: str) -> SlackCommandHandler:
    return SlackCommandHandler(
        state_root=state_root,
        memory=memory,
        owner_id=owner_id,
        slack_owner_user_id=OWNER_SLACK_ID,
        clock=lambda: NOW,
    )


def test_signature_verification_is_strict() -> None:
    body = _body("ship latest fine")
    headers = _signed(body)
    verify_slack_signature(
        signing_secret=SECRET,
        timestamp=headers["X-Slack-Request-Timestamp"],
        signature=headers["X-Slack-Signature"],
        body=body,
        now=NOW,
    )
    cases = {
        "signature_missing": (headers["X-Slack-Request-Timestamp"], None, body, NOW),
        "timestamp_invalid": ("soon", headers["X-Slack-Signature"], body, NOW),
        "timestamp_stale": (
            headers["X-Slack-Request-Timestamp"],
            headers["X-Slack-Signature"],
            body,
            NOW + timedelta(minutes=6),
        ),
        "signature_invalid": (
            headers["X-Slack-Request-Timestamp"],
            headers["X-Slack-Signature"],
            body + b"&extra=1",
            NOW,
        ),
    }
    for code, (timestamp, signature, payload, now) in cases.items():
        with pytest.raises(SlackCommandError, match=code) as failure:
            verify_slack_signature(
                signing_secret=SECRET,
                timestamp=timestamp,
                signature=signature,
                body=payload,
                now=now,
            )
        assert failure.value.status == 401
    with pytest.raises(SlackCommandError, match="signature_invalid"):
        verify_slack_signature(
            signing_secret="another-secret",
            timestamp=headers["X-Slack-Request-Timestamp"],
            signature=headers["X-Slack-Signature"],
            body=body,
            now=NOW,
        )


def test_command_grammar() -> None:
    parsed = parse_slash_command(_body("SHIP latest  looks right to me"))
    assert parsed.verdict is OwnerVerdict.SHIP and parsed.cycle == "latest"
    assert parsed.reason == "looks right to me" and parsed.user_id == OWNER_SLACK_ID
    explicit = parse_slash_command(_body(f"reject {CYCLE} not worth it"))
    assert explicit.verdict is OwnerVerdict.REJECT and explicit.cycle == str(CYCLE)
    implicit = parse_slash_command(_body("revise please also format tests"))
    assert implicit.cycle == "latest" and implicit.reason == "please also format tests"
    for text, code in (
        ("merge now", "command_unparsable"),
        ("", "command_unparsable"),
        ("ship latest", "reason_required"),
        ("ship", "reason_required"),
    ):
        with pytest.raises(SlackCommandError, match=code):
            parse_slash_command(_body(text))
    with pytest.raises(SlackCommandError, match="user_missing"):
        parse_slash_command(_body("ship latest ok", user=""))


def test_only_the_configured_owner_records_a_decision_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root, memory, owner_id = _state_with_request(tmp_path)
    handler = _handler(state_root, memory, owner_id)
    request_id = load_cycle_record(state_root).approval_request.request_id  # type: ignore[union-attr]

    monkeypatch.delenv("NEXUS_SOFTWARE_ENGINEER_SLACK_SIGNING_SECRET", raising=False)
    body = _body("ship latest the plan is right")
    reply = handler.handle(_signed(body), body)
    assert reply.status == 503 and "credentials_missing" in reply.text
    assert load_owner_decision(state_root, request_id) is None

    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_SLACK_SIGNING_SECRET", SECRET)
    forged = handler.handle(_signed(body, secret="wrong"), body)
    assert forged.status == 401 and "signature_invalid" in forged.text
    stranger_body = _body("ship latest let me in", user="U0STRANGER")
    stranger = handler.handle(_signed(stranger_body), stranger_body)
    assert stranger.status == 200 and "not_owner" in stranger.text
    assert load_owner_decision(state_root, request_id) is None

    recorded = handler.handle(_signed(body), body)
    assert recorded.status == 200 and recorded.decision is not None
    assert recorded.decision.verdict is OwnerVerdict.SHIP
    assert recorded.decision.channel == "slack"
    assert recorded.decision.decided_by.actor_id == owner_id
    assert "Nothing is published yet" in recorded.text and SECRET not in recorded.text
    persisted = load_owner_decision(state_root, request_id)
    assert persisted == recorded.decision
    memories = memory.load_all()
    assert any(
        item.status is EpistemicStatus.OWNER_DECISION and "owner decided ship" in item.content
        for item in memories
    )
    assert all(SECRET not in item.content for item in memories)

    # A replay of the very same signed request cannot decide twice.
    replay = handler.handle(_signed(body), body)
    assert replay.status == 200 and "already_decided" in replay.text
    unknown = _body(f"reject {UUID(int=1)} nope")
    assert "cycle_not_found" in handler.handle(_signed(unknown), unknown).text

    # No configured owner id means no Slack command is ever accepted.
    nobody = SlackCommandHandler(
        state_root=state_root,
        memory=memory,
        owner_id=owner_id,
        slack_owner_user_id=None,
        clock=lambda: NOW,
    )
    assert "not_owner" in nobody.handle(_signed(body), body).text


def test_fastapi_app_relays_replies_and_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root, memory, owner_id = _state_with_request(tmp_path)
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_SLACK_SIGNING_SECRET", SECRET)
    app = create_app(_handler(state_root, memory, owner_id))
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        body = _body("revise latest please add a test")
        response = client.post("/slack/commands", content=body, headers=_signed(body))
        assert response.status_code == 200
        payload = response.json()
        assert payload["response_type"] == "ephemeral"
        assert "Recorded REVISE" in payload["text"] and "will not publish" in payload["text"]
        bad = client.post("/slack/commands", content=body, headers=_signed(body, secret="x"))
        assert bad.status_code == 401 and "signature_invalid" in bad.json()["text"]
        unsigned = client.post("/slack/commands", content=body)
        assert unsigned.status_code == 401 and "signature_missing" in unsigned.json()["text"]


def test_cli_serve_refuses_without_owner_id_and_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    for key in list(os.environ):
        if key.startswith("NEXUS_SOFTWARE_ENGINEER_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["preflight"]) == 0
    assert "slack_owner_user_id_set=False" in capsys.readouterr().out
    assert cli.main(["serve-slack"]) == 2
    assert "SLACK_OWNER_USER_ID" in capsys.readouterr().err
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_SLACK_OWNER_USER_ID", OWNER_SLACK_ID)
    assert cli.main(["serve-slack"]) == 2
    assert "SLACK_SIGNING_SECRET is not set" in capsys.readouterr().err
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_SLACK_OWNER_USER_ID", "   ")
    assert SoftwareEngineerSettings(_env_file=None).slack_owner_user_id is None  # type: ignore[call-arg]
