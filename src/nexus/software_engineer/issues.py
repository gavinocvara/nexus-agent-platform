"""Read-only GitHub issue intake for the resident engineer.

Issues are the owner's and the public's backlog, and they are untrusted text: every title
and body is captured through ``UntrustedText`` (bounded, hashed, scanned for
instruction-shaped content) and becomes an observation the cycle can rank, never an
instruction it follows. The source reads with the ``issues: read`` scope at most, uses a
token only if one is present in the environment (never stored), excludes pull requests,
and turns every failure into a single warning signal instead of an exception, so a GitHub
outage cannot fail a cycle.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx

from nexus.software_engineer.publish import GITHUB_API_BASE, GITHUB_API_VERSION, PublishError
from nexus.software_engineer.publish import parse_github_repository as _parse_repository

GITHUB_READ_TOKEN_ENV_DEFAULT = "NEXUS_SOFTWARE_ENGINEER_GITHUB_READ_TOKEN"
DEFAULT_MAX_ISSUES = 20
MAX_ISSUES = 100
MAX_BODY_CHARS = 4000
MAX_TITLE_CHARS = 300
MAX_LABELS = 10


@dataclass(frozen=True, slots=True)
class IssueRecord:
    """One open issue as observed; every text field is untrusted data."""

    number: int
    title: str
    body: str
    labels: tuple[str, ...]
    author: str
    updated_at: str
    url: str


@dataclass(frozen=True, slots=True)
class IssueFetch:
    """What a fetch produced: issues, or a stable error code when nothing could be read."""

    issues: tuple[IssueRecord, ...]
    error_code: str | None = None
    truncated: bool = False


class IssueSource(Protocol):
    def fetch(self) -> IssueFetch: ...


class GitHubIssueSource:
    """List open issues (never pull requests) through the GitHub REST API, read-only."""

    def __init__(
        self,
        repository: str,
        *,
        token_env: str = GITHUB_READ_TOKEN_ENV_DEFAULT,
        api_base: str = GITHUB_API_BASE,
        max_issues: int = DEFAULT_MAX_ISSUES,
        timeout_seconds: float = 15.0,
        client_factory: Callable[[Mapping[str, str]], httpx.Client] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = _parse_repository(repository)
        if not api_base.startswith("https://"):
            raise PublishError("api_base_insecure")
        if not 1 <= max_issues <= MAX_ISSUES:
            raise ValueError(f"max_issues must be within 1..{MAX_ISSUES}")
        self.token_env = token_env
        self.api_base = api_base.rstrip("/")
        self.max_issues = max_issues
        self.timeout_seconds = timeout_seconds
        self._client_factory = client_factory or (
            lambda headers: httpx.Client(
                base_url=self.api_base, headers=dict(headers), timeout=self.timeout_seconds
            )
        )
        self.clock = clock

    def fetch(self) -> IssueFetch:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "nexus-resident-software-engineer",
        }
        token = os.environ.get(self.token_env, "").strip()
        if token and not any(c.isspace() or not c.isprintable() for c in token):
            headers["Authorization"] = f"Bearer {token}"
        del token
        try:
            with self._client_factory(headers) as client:
                response = client.get(
                    f"/repos/{self.repository}/issues",
                    params={
                        "state": "open",
                        "sort": "updated",
                        "direction": "desc",
                        "per_page": str(self.max_issues),
                    },
                )
        except httpx.HTTPError as exc:
            return IssueFetch(issues=(), error_code=f"transport_error:{type(exc).__name__}")
        finally:
            headers.pop("Authorization", None)
        if response.status_code == 401:
            return IssueFetch(issues=(), error_code="credentials_rejected")
        if response.status_code == 429 or (
            response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
        ):
            return IssueFetch(issues=(), error_code="rate_limited")
        if response.status_code != 200:
            return IssueFetch(issues=(), error_code=f"http_{response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return IssueFetch(issues=(), error_code="response_unreadable")
        if not isinstance(payload, list):
            return IssueFetch(issues=(), error_code="response_unreadable")
        issues = [
            record
            for item in payload
            if isinstance(item, dict) and (record := _issue_record(item)) is not None
        ]
        return IssueFetch(
            issues=tuple(issues[: self.max_issues]), truncated=len(payload) >= self.max_issues
        )


def _issue_record(item: Mapping[str, object]) -> IssueRecord | None:
    if "pull_request" in item:
        return None
    number = item.get("number")
    title = item.get("title")
    url = item.get("html_url")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        return None
    if not isinstance(title, str) or not isinstance(url, str) or not url.startswith("https://"):
        return None
    body = item.get("body")
    labels_raw = item.get("labels")
    labels: list[str] = []
    if isinstance(labels_raw, list):
        for label in labels_raw:
            name = label.get("name") if isinstance(label, dict) else label
            if isinstance(name, str) and name.strip():
                labels.append(name.strip()[:50])
    user = item.get("user")
    author = user.get("login") if isinstance(user, dict) else None
    updated = item.get("updated_at")
    return IssueRecord(
        number=number,
        title=title.strip()[:MAX_TITLE_CHARS],
        body=(body if isinstance(body, str) else "")[:MAX_BODY_CHARS],
        labels=tuple(labels[:MAX_LABELS]),
        author=(author if isinstance(author, str) else "unknown")[:100],
        updated_at=(updated if isinstance(updated, str) else "")[:40],
        url=url[:500],
    )


class StaticIssueSource:
    """A fixed fetch result for tests and evaluation."""

    def __init__(self, issues: Sequence[IssueRecord] = (), error_code: str | None = None) -> None:
        self.result = IssueFetch(issues=tuple(issues), error_code=error_code)

    def fetch(self) -> IssueFetch:
        return self.result


__all__ = [
    "DEFAULT_MAX_ISSUES",
    "GITHUB_READ_TOKEN_ENV_DEFAULT",
    "MAX_BODY_CHARS",
    "MAX_ISSUES",
    "GitHubIssueSource",
    "IssueFetch",
    "IssueRecord",
    "IssueSource",
    "StaticIssueSource",
]
