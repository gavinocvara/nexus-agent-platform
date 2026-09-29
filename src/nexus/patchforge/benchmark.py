"""PatchForge Benchmark v0: a small synthetic defect corpus with hidden ground truth.

Each task pairs an agent-visible engineering task with evaluator-only ground truth: the
accepted contents of the files that must change and the files that must not. The
``ContentOracleSandbox`` answers operator-profile commands by comparing worktree files
with that ground truth, so test outcomes reflect the real tree without executing
repository code. Its output never reveals the ground truth.

An independent evaluator then re-applies the attested patch artifact to a clean copy of
the source and checks the result against the same ground truth. A task is resolved only
when PatchForge proposed a patch and the evaluator confirms it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from pydantic import Field

from nexus.atlas.models import Sha256, StrictModel
from nexus.patchforge.canonical import canonical_json, canonical_sha256
from nexus.patchforge.e2e import (
    E2ERun,
    E2EScenario,
    FixtureRepository,
    PatchForgeE2EHarness,
    ScriptStep,
)
from nexus.patchforge.models import (
    PatchForgeFailure,
    PatchOutcome,
    RunBudgets,
)
from nexus.patchforge.policy import CommandPurpose, RepositoryProfile
from nexus.patchforge.sandbox import (
    SandboxError,
    SandboxExecution,
    SandboxRequest,
    SandboxStatus,
)
from nexus.patchforge.workspace import GitRunner

BENCHMARK_VERSION = "patchforge-benchmark-v0"
BENCHMARK_TIME = datetime(2026, 1, 1, tzinfo=UTC)
_ORACLE_PURPOSES = frozenset(
    {CommandPurpose.REPRODUCTION, CommandPurpose.TARGETED_TESTS, CommandPurpose.FULL_TEST_SUITE}
)


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """Evaluator-only truth. It never enters the engineering task or the agent's context."""

    accepted: Mapping[str, frozenset[str]]
    """Repository path -> SHA-256 digests of acceptable final contents."""

    unchanged: Sequence[str]
    """Paths (such as the tests that specify the behavior) that must not change."""

    def satisfied_by(self, read: Callable[[str], bytes | None]) -> bool:
        return all(
            (content := read(path)) is not None and sha256(content).hexdigest() in digests
            for path, digests in self.accepted.items()
        )


@dataclass(frozen=True, slots=True)
class BenchmarkTask:
    name: str
    fixture: FixtureRepository
    profile: RepositoryProfile
    title: str
    instructions: str
    acceptance_criteria: Sequence[str]
    truth: GroundTruth
    reference_solution: Mapping[str, str]
    """Scripted stand-in for a capable engine: path -> fixed content. Evaluator-side only."""


class ContentOracleSandbox:
    """Answer test commands from worktree contents; never execute repository code.

    The reproduction, targeted, and full-suite commands pass exactly when every
    ground-truth file has an accepted content. Formatter, linter, and typecheck commands
    always pass. Output is generic and never reveals the ground truth.
    """

    def __init__(
        self,
        profile: RepositoryProfile,
        truth: GroundTruth,
        *,
        clock: Callable[[], datetime],
        id_factory: Callable[[], UUID],
    ) -> None:
        self._purposes = {
            canonical_sha256(command): purpose for purpose, command in profile.commands.items()
        }
        self._truth = truth
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, request: SandboxRequest) -> SandboxExecution:
        command_sha = canonical_sha256(request.command)
        purpose = self._purposes.get(command_sha)
        if purpose is None:
            raise SandboxError("Oracle sandbox received a command outside the profile")
        passed = purpose not in _ORACLE_PURPOSES or self._truth.satisfied_by(
            lambda path: _read_regular_file(request.workspace, path)
        )
        started_at = self._clock()
        return SandboxExecution(
            execution_id=self._id_factory(),
            run_id=request.run_id,
            call_id=request.call_id,
            status=SandboxStatus.SUCCEEDED if passed else SandboxStatus.FAILED,
            exit_code=0 if passed else 1,
            stdout=b"1 passed\n" if passed else b"1 failed\n",
            stderr=b"" if passed else b"AssertionError: behavior does not match the tests\n",
            output_truncated=False,
            command_sha256=command_sha,
            policy_sha256=canonical_sha256(request.policy),
            started_at=started_at,
            completed_at=started_at + timedelta(seconds=0),
            error_code=None if passed else "command_failed",
        )


def _read_regular_file(root: Path, relative: str) -> bytes | None:
    """Read a regular file strictly inside ``root``; symlinks and escapes read as absent."""

    base = root.resolve()
    candidate = base.joinpath(*relative.split("/"))
    current = base
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink():
            return None
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base) or not resolved.is_file():
        return None
    return resolved.read_bytes()


EngineName = str
StepBuilder = Callable[[BenchmarkTask], list[ScriptStep]]


class TaskScore(StrictModel):
    task: str
    outcome: PatchOutcome
    failure: PatchForgeFailure | None
    resolved: bool
    false_proposal: bool
    tool_calls: int = Field(ge=0)
    implementation_loops: int = Field(ge=0)
    invariant_violations: list[str]
    result_sha256: Sha256


class BenchmarkReport(StrictModel):
    benchmark_version: str = BENCHMARK_VERSION
    engine: EngineName
    corpus_sha256: Sha256
    tasks: list[TaskScore]
    resolved: int = Field(ge=0)
    false_proposals: int = Field(ge=0)
    invariant_violations: int = Field(ge=0)
    outcomes: dict[str, int]

    @property
    def report_sha256(self) -> str:
        return canonical_sha256(self)


def corpus_sha256(tasks: Sequence[BenchmarkTask]) -> str:
    """Identity of the corpus, including ground truth, so results name what they measured."""

    return canonical_sha256(
        {
            "tasks": [
                {
                    "name": task.name,
                    "files": dict(sorted(task.fixture.files.items())),
                    "profile": task.profile.model_dump(mode="json"),
                    "title": task.title,
                    "instructions": task.instructions,
                    "acceptance": list(task.acceptance_criteria),
                    "accepted": {
                        path: sorted(items) for path, items in task.truth.accepted.items()
                    },
                    "unchanged": list(task.truth.unchanged),
                }
                for task in tasks
            ]
        }
    )


def evaluate(task: BenchmarkTask, run: E2ERun, work: Path, git: GitRunner) -> TaskScore:
    """Score one run independently of PatchForge's own conclusions."""

    result = run.result
    applied_ok = False
    if result.outcome is PatchOutcome.PATCH_PROPOSED and result.diff is not None:
        patch = run.artifacts.read(result.diff.patch_artifact)
        clone = work / "clone"
        source = run_source(run)
        git.run(["clone", "--quiet", "--no-hardlinks", "--", str(source), str(clone)])
        base = git.run(["-C", str(clone), "rev-parse", "HEAD"]).stdout_text().strip()
        if base == result.diff.base_sha:
            patch_file = work / "proposal.patch"
            patch_file.write_bytes(patch)
            git.run(["-C", str(clone), "apply", "--binary", "--", str(patch_file)])
            original = {path: _read_regular_file(source, path) for path in task.truth.unchanged}
            applied_ok = task.truth.satisfied_by(
                lambda path: _read_regular_file(clone, path)
            ) and all(
                _read_regular_file(clone, path) == content for path, content in original.items()
            )
    proposed = result.outcome is PatchOutcome.PATCH_PROPOSED
    return TaskScore(
        task=task.name,
        outcome=result.outcome,
        failure=result.failure,
        resolved=proposed and applied_ok,
        false_proposal=proposed and not applied_ok,
        tool_calls=len(result.tool_calls),
        implementation_loops=run.completion.snapshot.implementation_loops,
        invariant_violations=run.invariant_problems(),
        result_sha256=run.result_sha256,
    )


def run_source(run: E2ERun) -> Path:
    """The harness materializes each scenario's fixture repository at ``<root>/source``."""

    return run.root / "source"


def run_benchmark(
    tasks: Sequence[BenchmarkTask],
    engine: EngineName,
    steps: StepBuilder,
    budgets: RunBudgets,
    work_root: Path,
    *,
    now: datetime = BENCHMARK_TIME,
) -> BenchmarkReport:
    harness = PatchForgeE2EHarness(work_root / engine, now=now)
    git = GitRunner(work_root / "evaluator-git")
    scores: list[TaskScore] = []
    for task in tasks:
        scenario = E2EScenario(
            name=task.name,
            fixture=task.fixture,
            profile=task.profile,
            budgets=budgets,
            steps=steps(task),
            sandbox_plans=(),
            # Benchmarks score outcomes themselves; these fields are unused placeholders.
            expected_outcome=PatchOutcome.PATCH_PROPOSED,
            expected_failure=None,
            title=task.title,
            instructions=task.instructions,
            acceptance_criteria=tuple(task.acceptance_criteria),
            sandbox_factory=_oracle_factory(task.truth),
        )
        run = harness.run(scenario)
        evaluation = work_root / "evaluation" / engine / task.name
        evaluation.mkdir(parents=True)
        scores.append(evaluate(task, run, evaluation, git))
    outcomes: dict[str, int] = {}
    for score in scores:
        outcomes[score.outcome.value] = outcomes.get(score.outcome.value, 0) + 1
    return BenchmarkReport(
        engine=engine,
        corpus_sha256=corpus_sha256(tasks),
        tasks=scores,
        resolved=sum(score.resolved for score in scores),
        false_proposals=sum(score.false_proposal for score in scores),
        invariant_violations=sum(len(score.invariant_violations) for score in scores),
        outcomes=dict(sorted(outcomes.items())),
    )


def _oracle_factory(
    truth: GroundTruth,
) -> Callable[
    [RepositoryProfile, Callable[[], datetime], Callable[[], UUID]], ContentOracleSandbox
]:
    def build(
        profile: RepositoryProfile, clock: Callable[[], datetime], ids: Callable[[], UUID]
    ) -> ContentOracleSandbox:
        return ContentOracleSandbox(profile, truth, clock=clock, id_factory=ids)

    return build


def write_report(report: BenchmarkReport, output: Path) -> Path:
    """Persist a report atomically as canonical JSON named by engine."""

    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{report.engine}.json"
    temporary = output / f".{report.engine}.json.tmp"
    temporary.write_text(canonical_json(report) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


__all__ = [
    "BENCHMARK_TIME",
    "BENCHMARK_VERSION",
    "BenchmarkReport",
    "BenchmarkTask",
    "ContentOracleSandbox",
    "GroundTruth",
    "TaskScore",
    "corpus_sha256",
    "evaluate",
    "run_benchmark",
    "write_report",
]
