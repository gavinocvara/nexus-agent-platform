"""Harness tampering and harness shadowing are rejected before any test runs."""

from __future__ import annotations

from nexus.sentinelqa.models import (
    FINDING_CATEGORY,
    FindingCategory,
    SentinelFindingCode,
    is_evaluation_config_path,
)
from nexus.sentinelqa.tamper import HARNESS_PATTERNS, harness_tampering_lines


def _patch(path: str, added: list[str]) -> bytes:
    body = "\n".join(f"+{line}" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(added)} @@\n{body}\n"
    ).encode()


def _not_spec(path: str) -> bool:
    return path.startswith("tests/") or is_evaluation_config_path(path)


def test_shadowing_modules_and_packages_count_as_evaluation_configuration() -> None:
    for path in (
        "pytest.py",
        "src/pytest.py",
        "unittest.py",
        "src/pkg/_pytest/__init__.py",
        "pluggy/hooks.py",
        "vendor/site-packages/anything.py",
        "conftest.py",
        "deep/nested/conftest.py",
        "extra.pth",
        "pyproject.toml",
    ):
        assert is_evaluation_config_path(path), path
    for path in ("src/pkg/module.py", "tests/test_x.py", "README.md", "src/pytest_helpers.py"):
        assert not is_evaluation_config_path(path), path


def test_added_lines_that_reach_into_the_harness_are_found_per_pattern() -> None:
    cases = {
        "imports_test_harness": "import pytest",
        "edits_sys_modules": "sys.modules['tests.test_x'] = fake",
        "replaces_import": "builtins.__import__ = hook",
        "registers_pytest_plugin": "pytest_plugins = ['evil']",
        "reads_pytest_environment": "if os.environ.get('PYTEST_CURRENT_TEST'):",
        "hooks_assertion_rewriting": "from _pytest.assertion.rewrite import AssertionRewritingHook",
        "installs_tracer": "sys.settrace(tracer)",
        "rebinds_excepthook": "sys.excepthook = swallow",
    }
    assert set(cases) == {name for name, _ in HARNESS_PATTERNS}
    for name, line in cases.items():
        found = harness_tampering_lines(
            _patch("src/pkg/module.py", [line]), is_specification=_not_spec
        )
        assert any(item[1].startswith(f"{name}:") for item in found), (name, found)
        assert all(item[0] == "src/pkg/module.py" for item in found)


def test_removed_lines_specification_files_and_innocent_code_are_not_findings() -> None:
    removed = b"--- a/src/m.py\n+++ b/src/m.py\n@@ -1,1 +0,0 @@\n-import pytest\n"
    assert harness_tampering_lines(removed, is_specification=_not_spec) == []
    in_tests = _patch("tests/test_new.py", ["import pytest", "sys.modules['x'] = 1"])
    assert harness_tampering_lines(in_tests, is_specification=_not_spec) == []
    in_config = _patch("conftest.py", ["import pytest"])
    assert harness_tampering_lines(in_config, is_specification=_not_spec) == []
    innocent = _patch(
        "src/pkg/module.py",
        [
            "import sys",
            "def run_pytest_command(): return ['python', '-m', 'pytest']",
            "value = sys.modules_count  # attribute, not the registry",
            "name = 'unittest' if flag else 'pytest'",
            "import systems.pytesting as pt  # not the harness",
        ],
    )
    assert harness_tampering_lines(innocent, is_specification=_not_spec) == []
    deleted_file = b"--- a/src/m.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-import pytest\n"
    assert harness_tampering_lines(deleted_file, is_specification=_not_spec) == []


def test_findings_are_deduplicated_per_path_and_pattern_and_bounded() -> None:
    lines = ["import pytest"] * 5 + ["from unittest import mock"]
    found = harness_tampering_lines(_patch("src/a.py", lines), is_specification=_not_spec)
    assert len(found) == 1 and found[0][1].startswith("imports_test_harness:")
    many = b"".join(_patch(f"src/m{i}.py", ["import pytest"]) for i in range(40))
    assert len(harness_tampering_lines(many, is_specification=_not_spec)) == 20


def test_harness_tampering_is_a_rejection() -> None:
    assert FINDING_CATEGORY[SentinelFindingCode.HARNESS_TAMPERING] is FindingCategory.REJECTION
