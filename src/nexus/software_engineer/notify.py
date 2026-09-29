"""Owner notifications with a Slack adapter behind a transport boundary.

Credentials never enter this module's state: the Slack webhook URL is read from the
configured environment variable at send time and discarded. Delivery failures are
recorded as evidence and never raised into the engineering cycle. Minor events are
aggregated into one daily report; urgent events go out immediately, bounded per cycle.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Protocol
from uuid import UUID

import httpx
from pydantic import StringConstraints

from nexus.atlas.models import StrictModel
from nexus.brain.models import contains_secret
from nexus.software_engineer.models import URGENT_EVENTS, NotificationEvent, NotificationRecord

MAX_BODY_CHARS = 4000
Body = Annotated[str, StringConstraints(min_length=1, max_length=MAX_BODY_CHARS)]


class Notification(StrictModel):
    event: NotificationEvent
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    body: Body
    cycle_id: UUID
    urgent: bool = False

    @property
    def body_sha256(self) -> str:
        return sha256(self.body.encode("utf-8")).hexdigest()


class TransportError(RuntimeError):
    """The transport could not deliver; the message is never included."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class NotificationTransport(Protocol):
    @property
    def channel(self) -> str: ...

    def send(self, notification: Notification) -> None: ...


class NullTransport:
    """Records nothing and delivers nothing; used when notifications are disabled."""

    channel = "null"

    def send(self, notification: Notification) -> None:
        raise TransportError("notifications_disabled")


class RecordingTransport:
    """In-memory transport for tests; can fail the first N attempts."""

    channel = "recording"

    def __init__(self, *, fail_first: int = 0, fail_code: str = "transport_error") -> None:
        self.sent: list[Notification] = []
        self.attempts = 0
        self.fail_first = fail_first
        self.fail_code = fail_code

    def send(self, notification: Notification) -> None:
        self.attempts += 1
        if self.attempts <= self.fail_first:
            raise TransportError(self.fail_code)
        self.sent.append(notification)


class SlackWebhookTransport:
    """Post to a Slack incoming webhook whose URL lives only in the environment."""

    channel = "slack"

    def __init__(
        self,
        webhook_env: str,
        *,
        timeout_seconds: float = 10.0,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.webhook_env = webhook_env
        self.timeout_seconds = timeout_seconds
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=self.timeout_seconds)
        )

    def send(self, notification: Notification) -> None:
        url = os.environ.get(self.webhook_env, "").strip()
        if not url:
            raise TransportError("credentials_missing")
        if not url.startswith("https://"):
            raise TransportError("credentials_invalid")
        payload = {"text": f"*{notification.title}*\n{notification.body}"}
        try:
            with self._client_factory() as client:
                response = client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise TransportError("transport_error") from exc
        finally:
            del url
        if response.status_code == 429:
            raise TransportError("rate_limited")
        if response.status_code >= 400:
            raise TransportError(f"http_{response.status_code}")


class Notifier:
    """Deliver urgent events now, aggregate the rest, and record every attempt."""

    def __init__(
        self,
        transport: NotificationTransport,
        *,
        clock: Callable[[], datetime],
        max_per_cycle: int = 5,
        max_attempts: int = 3,
        sleeper: Callable[[float], None] | None = None,
        backoff_seconds: tuple[float, ...] = (0.5, 2.0),
    ) -> None:
        self.transport = transport
        self.clock = clock
        self.max_per_cycle = max_per_cycle
        self.max_attempts = max_attempts
        self.sleeper = sleeper or (lambda seconds: None)
        self.backoff = backoff_seconds
        self.records: list[NotificationRecord] = []
        self.queued: list[Notification] = []

    def notify(self, notification: Notification) -> NotificationRecord | None:
        """Send urgent events now; queue others for the daily report."""

        if notification.event in URGENT_EVENTS or notification.urgent:
            return self.send_now(notification)
        self.queued.append(notification)
        return None

    def send_now(self, notification: Notification) -> NotificationRecord:
        if contains_secret(notification.body) or contains_secret(notification.title):
            return self._record(notification, 0, False, "secret_blocked")
        if len(self.records) >= self.max_per_cycle:
            return self._record(notification, 0, False, "rate_limited_per_cycle")
        attempts = 0
        code: str | None = None
        while attempts < self.max_attempts:
            attempts += 1
            try:
                self.transport.send(notification)
            except TransportError as exc:
                code = exc.code
                if code in {"credentials_missing", "credentials_invalid", "notifications_disabled"}:
                    break
                if attempts < self.max_attempts:
                    self.sleeper(self.backoff[min(attempts - 1, len(self.backoff) - 1)])
                continue
            except Exception:  # noqa: BLE001 - a transport bug must not break the cycle
                code = "transport_exception"
                break
            return self._record(notification, attempts, True, None)
        return self._record(notification, attempts, False, code or "transport_error")

    def flush_daily_report(self, cycle_id: UUID, report_text: str) -> NotificationRecord:
        """One aggregated message: the daily report plus any queued minor events."""

        lines = [report_text.strip()]
        if self.queued:
            lines.append("")
            lines.append("Also this cycle:")
            lines.extend(f"- {item.title}" for item in self.queued)
        body = "\n".join(lines)[:MAX_BODY_CHARS]
        self.queued = []
        return self.send_now(
            Notification(
                event=NotificationEvent.DAILY_REPORT,
                title="NEXUS resident engineer: daily report",
                body=body or "No activity.",
                cycle_id=cycle_id,
            )
        )

    def _record(
        self, notification: Notification, attempts: int, delivered: bool, code: str | None
    ) -> NotificationRecord:
        record = NotificationRecord(
            event=notification.event,
            channel=self.transport.channel,
            title=notification.title,
            body_sha256=notification.body_sha256,
            attempts=attempts,
            delivered=delivered,
            error_code=None if delivered else code,
            sent_at=self.clock() if delivered else None,
        )
        self.records.append(record)
        return record


__all__ = [
    "MAX_BODY_CHARS",
    "Notification",
    "NotificationTransport",
    "Notifier",
    "NullTransport",
    "RecordingTransport",
    "SlackWebhookTransport",
    "TransportError",
]
