"""SentinelQA pytest summary parser: strict, and ``None`` whenever it cannot be sure."""

import pytest

from nexus.sentinelqa.models import SpecificationCounts
from nexus.sentinelqa.pytest_summary import parse_pytest_summary


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        (b"===== 3 passed in 0.12s =====\n", SpecificationCounts(passed=3)),
        (b"1 passed\n", SpecificationCounts(passed=1)),
        (b"1 failed\n", SpecificationCounts(failed=1)),
        (
            b"..F\n=== 2 passed, 1 failed, 1 skipped, 2 warnings in 1.50s ===\n",
            SpecificationCounts(passed=2, failed=1, skipped=1, warnings=2),
        ),
        (b"1 error in 0.01s\n", SpecificationCounts(errors=1)),
        (b"2 errors in 0.01s\n", SpecificationCounts(errors=2)),
        (
            b"3 passed, 1 xfailed, 1 xpassed, 4 deselected in 0.3s\n",
            SpecificationCounts(passed=3, xfailed=1, xpassed=1, deselected=4),
        ),
        (b"===== no tests ran in 0.01s =====\n", SpecificationCounts()),
        (b"1 passed in 0.01s (0:00:00)\n", SpecificationCounts(passed=1)),
        (b"1 passed in 0.02s\nsome trailing noise\n", SpecificationCounts(passed=1)),
    ],
)
def test_parses_pytest_summary_lines(stdout: bytes, expected: SpecificationCounts) -> None:
    assert parse_pytest_summary(stdout) == expected


@pytest.mark.parametrize(
    "stdout",
    [
        b"",
        b"Killed\n",
        b"passed\n",
        b"1 passed, 1 rerun in 0.1s\n",
        b"1 passed, 1 passed in 0.1s\n",
        b"3 warnings in 0.1s\n",
        b"\xff\xfe binary garbage\n",
    ],
)
def test_unknown_or_missing_summaries_are_not_counts(stdout: bytes) -> None:
    assert parse_pytest_summary(stdout) is None


def test_only_recent_lines_are_scanned() -> None:
    stdout = b"1 passed in 0.1s\n" + b"noise\n" * 500
    assert parse_pytest_summary(stdout) is None


def test_counts_total_and_not_passed() -> None:
    counts = SpecificationCounts(passed=2, failed=1, errors=1, skipped=1, xfailed=1, xpassed=1)
    assert counts.total == 7 and counts.not_passed == 5
    assert SpecificationCounts(deselected=4, warnings=2).total == 0
