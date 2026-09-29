"""Notifier: urgency, aggregation, retries, secret hygiene, and the Slack transport."""

from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from nexus.software_engineer.models import NotificationEvent
from nexus.software_engineer.notify import (
    Notification,
    Notifier,
    NullTransport,
    RecordingTransport,
    SlackWebhookTransport,
    TransportError,
)

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
CYCLE = UUID(int=5)


def _note(event: NotificationEvent, body: str = "details") -> Notification:
    return Notification(event=event, title=f"{event.value} title", body=body, cycle_id=CYCLE)


def test_urgent_events_go_now_and_minor_events_aggregate() -> None:
    transport = RecordingTransport()
    notifier = Notifier(transport, clock=lambda: NOW)
    urgent = notifier.notify(_note(NotificationEvent.APPROVAL_REQUIRED))
    assert urgent is not None and urgent.delivered and urgent.attempts == 1
    assert notifier.notify(_note(NotificationEvent.BUG_FIXED)) is None
    assert notifier.notify(_note(NotificationEvent.IMPROVEMENT_COMPLETED)) is None
    report = notifier.flush_daily_report(CYCLE, "# report")
    assert report.delivered and report.event is NotificationEvent.DAILY_REPORT
    assert len(transport.sent) == 2
    body = transport.sent[-1].body
    assert "bug_fixed title" in body and "improvement_completed title" in body
    assert notifier.queued == []
    assert [item.event for item in notifier.records] == [
        NotificationEvent.APPROVAL_REQUIRED,
        NotificationEvent.DAILY_REPORT,
    ]


def test_retries_then_delivers_and_never_raises() -> None:
    transport = RecordingTransport(fail_first=2)
    slept: list[float] = []
    notifier = Notifier(transport, clock=lambda: NOW, sleeper=slept.append)
    record = notifier.send_now(_note(NotificationEvent.BLOCKED))
    assert record.delivered and record.attempts == 3 and slept == [0.5, 2.0]
    exhausted = Notifier(RecordingTransport(fail_first=10), clock=lambda: NOW, sleeper=slept.append)
    failed = exhausted.send_now(_note(NotificationEvent.BLOCKED))
    assert not failed.delivered and failed.attempts == 3 and failed.error_code == "transport_error"
    missing = Notifier(NullTransport(), clock=lambda: NOW)
    record = missing.send_now(_note(NotificationEvent.BLOCKED))
    assert not record.delivered and record.attempts == 1
    assert record.error_code == "notifications_disabled"


def test_secrets_never_leave_and_per_cycle_limit_holds() -> None:
    transport = RecordingTransport()
    notifier = Notifier(transport, clock=lambda: NOW, max_per_cycle=2)
    blocked = notifier.send_now(
        _note(NotificationEvent.SECURITY_CONCERN, "key sk-abcdefghijklmnopqrstuvwxyz0123456789")
    )
    assert not blocked.delivered and blocked.error_code == "secret_blocked"
    assert transport.sent == []
    notifier.send_now(_note(NotificationEvent.BLOCKED))
    limited = notifier.send_now(_note(NotificationEvent.BLOCKED))
    assert not limited.delivered and limited.error_code == "rate_limited_per_cycle"
    assert len(transport.sent) == 1
    for record in notifier.records:
        assert "sk-" not in record.title


def test_slack_transport_reads_the_webhook_only_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    factory = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # noqa: E731
    transport = SlackWebhookTransport("TEST_SLACK_HOOK", client_factory=factory)
    monkeypatch.delenv("TEST_SLACK_HOOK", raising=False)
    with pytest.raises(TransportError) as missing:
        transport.send(_note(NotificationEvent.BLOCKED))
    assert missing.value.code == "credentials_missing"
    monkeypatch.setenv("TEST_SLACK_HOOK", "http://insecure.example/hook")
    with pytest.raises(TransportError) as insecure:
        transport.send(_note(NotificationEvent.BLOCKED))
    assert insecure.value.code == "credentials_invalid"
    monkeypatch.setenv("TEST_SLACK_HOOK", "https://hooks.slack.example/services/T/B/secret")
    transport.send(_note(NotificationEvent.BLOCKED, "body text"))
    assert seen and seen[0].url.host == "hooks.slack.example"
    assert b"body text" in seen[0].content and b"blocked title" in seen[0].content
    assert "secret" not in repr(transport.__dict__)

    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    limited = SlackWebhookTransport(
        "TEST_SLACK_HOOK",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(rate_limited)),
    )
    with pytest.raises(TransportError) as rate:
        limited.send(_note(NotificationEvent.BLOCKED))
    assert rate.value.code == "rate_limited"

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    down = SlackWebhookTransport(
        "TEST_SLACK_HOOK",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(broken)),
    )
    with pytest.raises(TransportError) as outage:
        down.send(_note(NotificationEvent.BLOCKED))
    assert outage.value.code == "transport_error"
