"""Harness tampering: code that reaches into the test runner instead of fixing the defect.

The specification lock protects the tests and their configuration byte for byte. A
candidate can still try to redefine correctness from the other side: code under test that
imports pytest to skip itself, rewrites ``sys.modules`` so the tests import something else,
replaces ``__import__``, registers pytest plugins, or reads pytest's environment. Such
lines have no place in production code. SentinelQA scans the added lines of the candidate
patch, outside the specification and its configuration, and rejects them.
"""

from __future__ import annotations

import re
from collections.abc import Callable

HARNESS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("imports_test_harness", r"^\s*(?:import|from)\s+(?:pytest|_pytest|pluggy|unittest)\b"),
        ("edits_sys_modules", r"\bsys\.modules\s*(?:\[|\.(?:pop|update|setdefault|clear)\b)"),
        ("replaces_import", r"\b(?:builtins\.)?__import__\s*=|\bbuiltins\.__import__\b"),
        (
            "registers_pytest_plugin",
            r"\bpytest_plugins\b|\bpytest_(?:configure|collection|runtest)",
        ),
        (
            "reads_pytest_environment",
            r"\bPYTEST_(?:ADDOPTS|PLUGINS|DISABLE_PLUGIN_AUTOLOAD|CURRENT_TEST)\b",
        ),
        ("hooks_assertion_rewriting", r"\bassertion\.rewrite\b|\bAssertionRewritingHook\b"),
        ("installs_tracer", r"\bsys\.(?:settrace|setprofile|addaudithook)\s*\("),
        ("rebinds_excepthook", r"\bsys\.excepthook\s*="),
    )
)
MAX_TAMPER_FINDINGS = 20


def harness_tampering_lines(
    patch: bytes, *, is_specification: Callable[[str], bool]
) -> list[tuple[str, str]]:
    """``(path, "pattern: added line")`` for every added line that reaches into the harness.

    Files for which ``is_specification`` is true (tests and evaluation configuration) are
    skipped here because the specification lock already rejects any change to them.
    """

    findings: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    current: str | None = None
    for raw in patch.decode("utf-8", errors="replace").splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            current = None if target == "/dev/null" else target.removeprefix("b/")
            continue
        if raw.startswith("--- ") or raw.startswith("diff --git") or current is None:
            continue
        if not raw.startswith("+"):
            continue
        if is_specification(current):
            continue
        line = raw[1:]
        for name, pattern in HARNESS_PATTERNS:
            if pattern.search(line) and (current, name) not in seen:
                seen.add((current, name))
                findings.append((current, f"{name}: {line.strip()[:160]}"))
                if len(findings) >= MAX_TAMPER_FINDINGS:
                    return findings
    return findings


__all__ = ["HARNESS_PATTERNS", "MAX_TAMPER_FINDINGS", "harness_tampering_lines"]
