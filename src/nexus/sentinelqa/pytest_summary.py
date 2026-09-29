"""Strict parser for pytest's final summary line.

SentinelQA accounts for tests only from the runner's own summary. When no summary can be
parsed, or it contains a token this parser does not know, the result is ``None`` and the
caller must treat the run as incomplete evidence rather than guess.
"""

from __future__ import annotations

import re

from nexus.sentinelqa.models import SpecificationCounts

_TOKEN = re.compile(
    r"^(?P<count>\d+) (?P<kind>passed|failed|errors?|skipped|xfailed|xpassed|deselected|"
    r"warnings?)$"
)
_DURATION = re.compile(r"\s+in\s+\d+(?:\.\d+)?s(?:\s+\([^)]*\))?$")
_NO_TESTS = re.compile(r"^no tests ran(?:\s+in\s+\d+(?:\.\d+)?s)?$")
_FIELD = {
    "passed": "passed",
    "failed": "failed",
    "error": "errors",
    "errors": "errors",
    "skipped": "skipped",
    "xfailed": "xfailed",
    "xpassed": "xpassed",
    "deselected": "deselected",
    "warning": "warnings",
    "warnings": "warnings",
}
MAX_SUMMARY_SCAN_LINES = 200


def parse_pytest_summary(stdout: bytes) -> SpecificationCounts | None:
    """Return the counts from the last pytest summary line, or ``None`` if there is none."""

    text = stdout.decode("utf-8", errors="replace")
    lines = text.splitlines()[-MAX_SUMMARY_SCAN_LINES:]
    for raw in reversed(lines):
        line = raw.strip().strip("=").strip()
        if not line:
            continue
        line = _DURATION.sub("", line)
        if _NO_TESTS.match(line):
            return SpecificationCounts()
        counts = _parse_tokens(line)
        if counts is not None:
            return counts
    return None


def _parse_tokens(line: str) -> SpecificationCounts | None:
    values: dict[str, int] = {}
    for token in line.split(", "):
        match = _TOKEN.match(token.strip())
        if match is None:
            return None
        field = _FIELD[match.group("kind")]
        if field in values:
            return None
        values[field] = int(match.group("count"))
    if not values or set(values) == {"warnings"}:
        return None
    return SpecificationCounts(**values)


__all__ = ["MAX_SUMMARY_SCAN_LINES", "parse_pytest_summary"]
