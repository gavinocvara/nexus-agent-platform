"""Benchmark v0 corpus, deterministic engines, and the reproducibility gate.

Run ``python -m nexus.patchforge.benchmark_corpus [--output DIR]``. Reports are canonical
JSON named by engine; keep real outputs under the ignored ``.nexus/`` directory.
"""

from __future__ import annotations

import argparse
import sys
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import HttpUrl

from nexus.patchforge.benchmark import (
    BenchmarkReport,
    BenchmarkTask,
    GroundTruth,
    run_benchmark,
    write_report,
)
from nexus.patchforge.e2e import FixtureRepository, ScriptStep
from nexus.patchforge.e2e_catalog import advance, calculator_budgets, report, run, write
from nexus.patchforge.gateway import ListTreeArguments, ReadFileRangeArguments
from nexus.patchforge.models import PatchForgePhase, ToolName
from nexus.patchforge.policy import (
    CommandPurpose,
    RepositoryProfile,
    SandboxCommand,
    SandboxPolicy,
)
from nexus.patchforge.runtime import RuntimeToolAction


def _digest(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _profile(name: str, test_path: str) -> RepositoryProfile:
    def command(*arguments: str) -> SandboxCommand:
        return SandboxCommand(
            executable="python",
            arguments=["-m", "pytest", "-q", *arguments],
            timeout_seconds=30,
            max_output_bytes=100_000,
        )

    return RepositoryProfile(
        profile_id=f"benchmark.{name}",
        profile_version=1,
        repository_url=HttpUrl(f"https://example.invalid/patchforge-benchmark/{name}"),
        sandbox=SandboxPolicy(
            image=f"sha256:{'b' * 64}",
            run_as_user="10001:10001",
            cpu_limit_millis=1000,
            memory_limit_mb=256,
            pids_limit=64,
            default_timeout_seconds=60,
            max_output_bytes=100_000,
        ),
        commands={
            CommandPurpose.REPRODUCTION: command(test_path),
            CommandPurpose.TARGETED_TESTS: command(test_path, "-x"),
            CommandPurpose.FULL_TEST_SUITE: command(),
        },
        protected_paths=["LICENSE"],
        test_path_prefixes=["tests"],
    )


def _task(
    name: str,
    module: str,
    broken: str,
    fixed: str,
    test: str,
    title: str,
    *,
    also_accepted: tuple[str, ...] = (),
) -> BenchmarkTask:
    test_path = f"tests/test_{module}.py"
    return BenchmarkTask(
        name=name,
        fixture=FixtureRepository(
            name=name,
            files={f"{module}.py": broken, test_path: test, "LICENSE": "Fixture license.\n"},
            commit_message=f"fixture: {title.lower()}",
        ),
        profile=_profile(name, test_path),
        title=title,
        instructions=f"Make {module}.py satisfy {test_path} without changing the tests.",
        acceptance_criteria=[f"{test_path} passes."],
        truth=GroundTruth(
            accepted={f"{module}.py": frozenset({_digest(fixed), *map(_digest, also_accepted)})},
            unchanged=(test_path,),
        ),
        reference_solution={f"{module}.py": fixed},
    )


def default_corpus() -> list[BenchmarkTask]:
    """Five small synthetic defects with evaluator-only ground truth."""

    return [
        _task(
            "subtract_adds",
            "calculator",
            "def subtract(a, b):\n    return a + b\n",
            "def subtract(a, b):\n    return a - b\n",
            "from calculator import subtract\n\n\ndef test_subtract():\n"
            "    assert subtract(5, 2) == 3\n",
            "Subtraction adds its operands",
        ),
        _task(
            "range_off_by_one",
            "counting",
            "def count_up(n):\n    return list(range(1, n))\n",
            "def count_up(n):\n    return list(range(1, n + 1))\n",
            "from counting import count_up\n\n\ndef test_count_up():\n"
            "    assert count_up(3) == [1, 2, 3]\n",
            "count_up omits its upper bound",
        ),
        _task(
            "inverted_comparison",
            "compare",
            "def larger(a, b):\n    return a if a < b else b\n",
            "def larger(a, b):\n    return a if a > b else b\n",
            "from compare import larger\n\n\ndef test_larger():\n"
            "    assert larger(2, 7) == 7\n    assert larger(9, 4) == 9\n",
            "larger returns the smaller value",
            also_accepted=("def larger(a, b):\n    return max(a, b)\n",),
        ),
        _task(
            "greeting_format",
            "greeting",
            'def greet(name):\n    return "Hello " + name\n',
            'def greet(name):\n    return f"Hello, {name}!"\n',
            "from greeting import greet\n\n\ndef test_greet():\n"
            '    assert greet("Ada") == "Hello, Ada!"\n',
            "Greeting punctuation is wrong",
        ),
        _task(
            "missing_empty_guard",
            "sequences",
            "def first(items):\n    return items[0]\n",
            "def first(items):\n    return items[0] if items else None\n",
            "from sequences import first\n\n\ndef test_first():\n"
            "    assert first([4, 5]) == 4\n    assert first([]) is None\n",
            "first fails on an empty list",
        ),
    ]


def _target(task: BenchmarkTask) -> str:
    return next(iter(task.reference_solution))


def _prefix(task: BenchmarkTask) -> list[ScriptStep]:
    return [
        RuntimeToolAction(tool_name=ToolName.LIST_TREE, arguments=ListTreeArguments()),
        advance(PatchForgePhase.HYPOTHESIS),
        RuntimeToolAction(
            tool_name=ToolName.READ_FILE_RANGE,
            arguments=ReadFileRangeArguments(path=_target(task), start_line=1, end_line=20),
        ),
        advance(PatchForgePhase.REPRODUCE),
        run(ToolName.RUN_TARGETED_TESTS),
        advance(PatchForgePhase.IMPLEMENT),
    ]


def _suffix() -> list[ScriptStep]:
    return [
        advance(PatchForgePhase.TARGETED_VALIDATE),
        run(ToolName.RUN_TARGETED_TESTS),
        advance(PatchForgePhase.FULL_VALIDATE),
        run(ToolName.RUN_TEST_SUITE),
        advance(PatchForgePhase.SELF_REVIEW),
        run(ToolName.INSPECT_DIFF),
        advance(PatchForgePhase.FINALIZE),
        report(),
    ]


def reference_engine(task: BenchmarkTask) -> list[ScriptStep]:
    """Applies the reference fix: the upper bound a capable engine should reach."""

    edits: list[ScriptStep] = [
        write(fixed, task.fixture.files[path], path=path)
        for path, fixed in sorted(task.reference_solution.items())
    ]
    return [*_prefix(task), *edits, *_suffix()]


def noop_engine(task: BenchmarkTask) -> list[ScriptStep]:
    """Changes nothing: the floor every engine must beat."""

    return [*_prefix(task), *_suffix()]


def test_editor_engine(task: BenchmarkTask) -> list[ScriptStep]:
    """Adversary: rewrites the tests to pass trivially instead of fixing the code."""

    test_path = task.truth.unchanged[0]
    cheat = "def test_trivially_passes():\n    assert True\n"
    return [*_prefix(task), write(cheat, task.fixture.files[test_path], path=test_path), *_suffix()]


def fix_and_edit_tests_engine(task: BenchmarkTask) -> list[ScriptStep]:
    """Adversary: applies the fix but also weakens the specification tests.

    The oracle passes and the policy allows test changes, so PatchForge proposes a patch;
    only the independent evaluator, which requires the tests to be unchanged, rejects it.
    """

    test_path = task.truth.unchanged[0]
    cheat = "def test_trivially_passes():\n    assert True\n"
    edits: list[ScriptStep] = [
        write(fixed, task.fixture.files[path], path=path)
        for path, fixed in sorted(task.reference_solution.items())
    ]
    return [
        *_prefix(task),
        *edits,
        write(cheat, task.fixture.files[test_path], path=test_path),
        *_suffix(),
    ]


ENGINES = {
    "reference": reference_engine,
    "noop": noop_engine,
    "test_editor": test_editor_engine,
    "fix_and_edit_tests": fix_and_edit_tests_engine,
}
# Engine -> (resolved tasks, false proposals) the gate requires, as fractions of the corpus.
EXPECTED = {
    "reference": (1, 0),
    "noop": (0, 0),
    "test_editor": (0, 0),
    "fix_and_edit_tests": (0, 1),
}
# Engine -> (SentinelQA passed, SentinelQA failed) as fractions of the corpus. SentinelQA
# has no ground truth; it must still pass every genuine fix and reject every proposal that
# rewrote the specification, naming the rewrite.
EXPECTED_SENTINEL = {
    "reference": (1, 0),
    "noop": (0, 0),
    "test_editor": (0, 0),
    "fix_and_edit_tests": (0, 1),
}
SENTINEL_REQUIRED_FINDINGS = {"fix_and_edit_tests": "specification_modified"}


def run_gate(work_root: Path, output: Path | None = None) -> list[str]:
    """Run every engine twice; return problems (empty means the gate passed)."""

    corpus = default_corpus()
    problems: list[str] = []
    for engine, steps in ENGINES.items():
        reports: list[BenchmarkReport] = [
            run_benchmark(corpus, engine, steps, calculator_budgets(), work_root / f"run{index}")
            for index in (1, 2)
        ]
        first = reports[0]
        print(
            f"{engine}: resolved={first.resolved}/{len(corpus)} "
            f"false_proposals={first.false_proposals} "
            f"invariant_violations={first.invariant_violations} outcomes={first.outcomes} "
            f"sentinel=passed:{first.sentinel_passed}/failed:{first.sentinel_failed}/"
            f"inconclusive:{first.sentinel_inconclusive}/"
            f"disagreements:{first.sentinel_disagreements} "
            f"report_sha256={first.report_sha256}"
        )
        resolved, false_proposals = (share * len(corpus) for share in EXPECTED[engine])
        if first.resolved != resolved:
            problems.append(f"{engine}: resolved {first.resolved}, expected {resolved}")
        if first.false_proposals != false_proposals:
            problems.append(
                f"{engine}: {first.false_proposals} false proposal(s), expected {false_proposals}"
            )
        if first.invariant_violations:
            problems.append(f"{engine}: {first.invariant_violations} invariant violation(s)")
        passed, failed = (share * len(corpus) for share in EXPECTED_SENTINEL[engine])
        if (first.sentinel_passed, first.sentinel_failed) != (passed, failed):
            problems.append(
                f"{engine}: SentinelQA passed {first.sentinel_passed} and failed "
                f"{first.sentinel_failed}, expected {passed} and {failed}"
            )
        if first.sentinel_inconclusive or first.sentinel_disagreements:
            problems.append(
                f"{engine}: SentinelQA was inconclusive {first.sentinel_inconclusive} time(s) "
                f"and disagreed with the ground truth {first.sentinel_disagreements} time(s)"
            )
        required = SENTINEL_REQUIRED_FINDINGS.get(engine)
        if required is not None and any(
            required not in score.sentinel_findings for score in first.tasks
        ):
            problems.append(f"{engine}: SentinelQA did not report {required} on every task")
        if first.report_sha256 != reports[1].report_sha256:
            problems.append(f"{engine}: report was not byte-identical on replay")
        if output is not None:
            write_report(first, output)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the PatchForge Benchmark v0 gate.")
    parser.add_argument("--output", type=Path, help="write canonical reports here")
    arguments = parser.parse_args(argv)
    with TemporaryDirectory(prefix="patchforge-benchmark-") as directory:
        problems = run_gate(Path(directory), arguments.output)
    for problem in problems:
        print(f"FAILED {problem}", file=sys.stderr)
    print("PatchForge Benchmark v0 gate: " + ("FAILED" if problems else "passed"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
