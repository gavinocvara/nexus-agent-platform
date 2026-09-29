"""PatchForge Benchmark v0: oracle sandbox, ground-truth isolation, evaluator, and gate."""

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from nexus.patchforge import benchmark_corpus
from nexus.patchforge.benchmark import (
    ContentOracleSandbox,
    corpus_sha256,
    run_benchmark,
    write_report,
)
from nexus.patchforge.benchmark_corpus import ENGINES, default_corpus
from nexus.patchforge.canonical import canonical_json
from nexus.patchforge.e2e_catalog import calculator_budgets
from nexus.patchforge.models import PatchOutcome
from nexus.patchforge.policy import CommandPurpose, SandboxCommand
from nexus.patchforge.sandbox import SandboxError, SandboxRequest, SandboxStatus

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _oracle(tmp_path: Path) -> tuple[ContentOracleSandbox, SandboxRequest]:
    task = default_corpus()[0]
    sandbox = ContentOracleSandbox(
        task.profile, task.truth, clock=lambda: NOW, id_factory=lambda: UUID(int=7)
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    for path, content in task.fixture.files.items():
        target = worktree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    request = SandboxRequest(
        run_id=UUID(int=1),
        call_id=UUID(int=2),
        workspace=worktree,
        command=task.profile.commands[CommandPurpose.TARGETED_TESTS],
        policy=task.profile.sandbox,
    )
    return sandbox, request


def test_oracle_scores_the_tree_without_revealing_ground_truth(tmp_path: Path) -> None:
    sandbox, request = _oracle(tmp_path)
    task = default_corpus()[0]
    failed = sandbox.execute(request)
    assert failed.status is SandboxStatus.FAILED and failed.exit_code == 1
    (request.workspace / "calculator.py").write_text(
        task.reference_solution["calculator.py"], encoding="utf-8"
    )
    passed = sandbox.execute(request)
    assert passed.status is SandboxStatus.SUCCEEDED
    for execution in (failed, passed):
        output = execution.stdout + execution.stderr
        assert b"a - b" not in output
        assert not any(digest.encode() in output for digest in task.truth.accepted["calculator.py"])


def test_oracle_rejects_unknown_commands_and_ignores_symlinked_targets(tmp_path: Path) -> None:
    sandbox, request = _oracle(tmp_path)
    foreign = request.model_copy(
        update={
            "command": SandboxCommand(
                executable="python", arguments=["-c", "0"], timeout_seconds=5, max_output_bytes=10
            )
        }
    )
    with pytest.raises(SandboxError, match="outside the profile"):
        sandbox.execute(foreign)
    task = default_corpus()[0]
    outside = tmp_path / "fixed.py"
    outside.write_text(task.reference_solution["calculator.py"], encoding="utf-8")
    target = request.workspace / "calculator.py"
    target.unlink()
    os.symlink(outside, target)
    assert sandbox.execute(request).status is SandboxStatus.FAILED


def test_engineering_task_never_contains_ground_truth() -> None:
    for task in default_corpus():
        visible = task.title + task.instructions + "".join(task.acceptance_criteria)
        for fixed in task.reference_solution.values():
            assert fixed not in visible
        for digests in task.truth.accepted.values():
            assert not any(digest in visible for digest in digests)


def test_evaluator_rejects_a_proposal_that_rewrote_the_tests(tmp_path: Path) -> None:
    corpus = default_corpus()[:1]
    report = run_benchmark(
        corpus,
        "fix_and_edit_tests",
        ENGINES["fix_and_edit_tests"],
        calculator_budgets(),
        tmp_path,
    )
    score = report.tasks[0]
    assert score.outcome is PatchOutcome.PATCH_PROPOSED
    assert score.resolved is False and score.false_proposal is True
    assert score.invariant_violations == []


def test_reports_are_deterministic_and_named_by_corpus(tmp_path: Path) -> None:
    corpus = default_corpus()
    first = run_benchmark(
        corpus, "reference", ENGINES["reference"], calculator_budgets(), tmp_path / "a"
    )
    second = run_benchmark(
        corpus, "reference", ENGINES["reference"], calculator_budgets(), tmp_path / "b"
    )
    assert first == second and first.report_sha256 == second.report_sha256
    assert first.corpus_sha256 == corpus_sha256(corpus)
    assert first.resolved == len(corpus)
    written = write_report(first, tmp_path / "out")
    assert written.read_text(encoding="utf-8") == canonical_json(first) + "\n"


def test_gate_passes_and_fails_on_a_wrong_expectation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert benchmark_corpus.run_gate(tmp_path / "ok") == []
    monkeypatch.setitem(benchmark_corpus.EXPECTED, "noop", (1, 0))
    problems = benchmark_corpus.run_gate(tmp_path / "wrong")
    assert problems == ["noop: resolved 0, expected 5"]
