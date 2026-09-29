"""Deterministic SentinelQA harness: review PatchForge E2E candidates with scripted sandboxes.

A scenario runs a PatchForge E2E scenario to obtain a candidate, captures the
specification lock from the untouched source repository, optionally tampers with the
evidence (source history, lock, artifact) the way an adversary or an outage would, and
then reviews the candidate with a scripted or oracle sandbox. Fixed clocks and
content-derived identifiers make every verdict replay byte for byte.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

from nexus.atlas.models import ReviewVerdict
from nexus.patchforge.attestor import LocalArtifactStore
from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.e2e import (
    E2ERun,
    E2EScenario,
    PatchForgeE2EHarness,
    SandboxFactory,
    SandboxHook,
)
from nexus.patchforge.policy import CommandPurpose, RepositoryProfile
from nexus.patchforge.sandbox import (
    FakeSandbox,
    FakeSandboxPlan,
    SandboxExecution,
    SandboxExecutor,
    SandboxRequest,
    SandboxStatus,
)
from nexus.patchforge.workspace import GitRunner
from nexus.sentinelqa.lock import capture_specification_lock
from nexus.sentinelqa.models import (
    FindingCategory,
    SentinelVerdict,
    SpecificationLock,
    SpecificationTree,
)
from nexus.sentinelqa.verifier import SentinelQAVerifier

_SENTINEL_NAMESPACE = UUID("b4e2c7a9-1d3f-4b5e-8a6c-9f0e1d2c3b4a")
MAX_SPECIFICATION_RUNS = 5


@dataclass(slots=True)
class SentinelContext:
    """Everything a scenario may tamper with between the PatchForge run and the review."""

    run: E2ERun
    lock: SpecificationLock
    source: Path
    git: GitRunner
    artifacts: LocalArtifactStore


Tamper = Callable[[SentinelContext], SentinelContext]


@dataclass(frozen=True, slots=True)
class SentinelScenario:
    """One candidate, one scripted SentinelQA sandbox, and the verdict evidence must produce."""

    name: str
    candidate: E2EScenario
    expected_verdict: ReviewVerdict
    plans: Sequence[FakeSandboxPlan] = ()
    """Scripted answers, in order: pristine reproduction, targeted, full suite; then
    verification targeted and full suite."""

    expected_findings: Sequence[str] = ()
    forbidden_findings: Sequence[str] = ()
    tamper: Tamper | None = None
    sandbox_factory: SandboxFactory | None = None
    sandbox_hook: SandboxHook | None = None
    """Fault injection inside the Nth SentinelQA execution, before its result."""


@dataclass(frozen=True, slots=True)
class SentinelRun:
    scenario: SentinelScenario
    candidate: E2ERun
    lock: SpecificationLock
    verdict: SentinelVerdict
    root: Path
    source_refs_before: str
    source_refs_after: str
    requests: Sequence[SandboxRequest] = field(default=(), repr=False)

    @property
    def verdict_sha256(self) -> str:
        return canonical_sha256(self.verdict)

    def problems(self) -> list[str]:
        """Scenario expectations plus the invariants every SentinelQA review must satisfy."""

        scenario = self.scenario
        verdict = self.verdict
        problems: list[str] = []
        codes = set(verdict.finding_codes)

        def check(condition: bool, message: str) -> None:
            if not condition:
                problems.append(message)

        check(verdict.verdict is scenario.expected_verdict, "unexpected verdict")
        check(set(scenario.expected_findings) <= codes, "expected finding missing")
        check(not (set(scenario.forbidden_findings) & codes), "forbidden finding present")
        return problems + self.invariant_problems()

    def invariant_problems(self) -> list[str]:
        verdict = self.verdict
        problems: list[str] = []

        def check(condition: bool, message: str) -> None:
            if not condition:
                problems.append(message)

        profile = self.candidate.scenario.profile
        commands = {canonical_sha256(command) for command in profile.commands.values()}
        policy_sha = canonical_sha256(profile.sandbox)
        # SentinelQA runs only the operator's commands under the operator's sandbox policy.
        check(
            all(item.command_sha256 in commands for item in verdict.runs),
            "a run used a command outside the operator profile",
        )
        check(
            all(item.policy_sha256 == policy_sha for item in verdict.runs),
            "a run used another sandbox policy",
        )
        check(
            all(
                item.policy.network_disabled and not item.policy.secrets_allowed
                for item in self.requests
            ),
            "a sandbox request allowed network or secrets",
        )
        check(len(verdict.runs) <= MAX_SPECIFICATION_RUNS, "too many specification runs")
        # The review never touches the source repository: no push, commit, or ref change.
        check(self.source_refs_after == self.source_refs_before, "source repository changed")
        # Evidence authority: runs are attested by SentinelQA and bound to this review.
        check(
            all(item.attested_by == "sentinelqa.runtime" for item in verdict.runs),
            "a run is not attested by SentinelQA",
        )
        check(verdict.lock_sha256 == self.lock.lock_sha256, "verdict names another lock")
        # Fail closed: a pass needs an intact specification and passing pristine runs on a
        # fresh, unchanged verification tree; anything unverifiable is never a pass.
        unverifiable = any(
            item.category is FindingCategory.UNVERIFIABLE for item in verdict.findings
        )
        check(
            not (unverifiable and verdict.verdict is not ReviewVerdict.INCONCLUSIVE),
            "unverifiable evidence did not fail closed",
        )
        if verdict.verdict is ReviewVerdict.PASSED:
            check(
                verdict.integrity is not None and verdict.integrity.specification_intact,
                "passed with a changed specification",
            )
            verification = [
                item for item in verdict.runs if item.tree is SpecificationTree.VERIFICATION
            ]
            check(
                {item.purpose for item in verification}
                >= {CommandPurpose.TARGETED_TESTS, CommandPurpose.FULL_TEST_SUITE},
                "passed without both verification runs",
            )
            check(all(item.tree_unchanged for item in verification), "passed on a mutated tree")
        return problems


class _RecordingSandbox:
    def __init__(self, inner: SandboxExecutor, hook: SandboxHook | None) -> None:
        self.inner = inner
        self.hook = hook
        self.requests: list[SandboxRequest] = []

    def execute(self, request: SandboxRequest) -> SandboxExecution:
        self.requests.append(request)
        if self.hook is not None:
            self.hook(len(self.requests), request.workspace)
        return self.inner.execute(request)


class SentinelQAHarness:
    """Run SentinelQA scenarios in a caller-owned directory with a fixed clock."""

    def __init__(self, work_root: Path, *, now: datetime) -> None:
        if now.utcoffset() is None:
            raise ValueError("Harness time must be timezone-aware")
        self.work_root = work_root
        self.now = now

    def run(self, scenario: SentinelScenario) -> SentinelRun:
        root = self.work_root / scenario.name
        root.mkdir(parents=True)
        candidate = PatchForgeE2EHarness(root / "patchforge", now=self.now).run(scenario.candidate)
        source = candidate.root / "source"
        git = GitRunner(root / "git-sentinel")
        profile = candidate.scenario.profile
        lock = capture_specification_lock(
            git, source, candidate.source_sha, profile, captured_at=self.now
        )
        context = SentinelContext(
            run=candidate, lock=lock, source=source, git=git, artifacts=candidate.artifacts
        )
        if scenario.tamper is not None:
            context = scenario.tamper(context)
        refs_before = _source_refs(git, context.source)
        review_id = uuid5(_SENTINEL_NAMESPACE, f"{scenario.name}:review")
        execution_ids = _counter(scenario.name, "execution")
        inner: SandboxExecutor = (
            scenario.sandbox_factory(profile, self._clock, execution_ids)
            if scenario.sandbox_factory is not None
            else FakeSandbox(scenario.plans, clock=self._clock, id_factory=execution_ids)
        )
        sandbox = _RecordingSandbox(inner, scenario.sandbox_hook)
        verifier = SentinelQAVerifier(
            task=context.run.task,
            profile=profile,
            lock=context.lock,
            artifact_store=context.artifacts,
            sandbox=sandbox,
            git=git,
            clock=self._clock,
            review_id=review_id,
            work_root=root / "review",
        )
        verdict = verifier.review(context.run.result, context.source)
        return SentinelRun(
            scenario=scenario,
            candidate=context.run,
            lock=context.lock,
            verdict=verdict,
            root=root,
            source_refs_before=refs_before,
            source_refs_after=_source_refs(git, context.source),
            requests=tuple(sandbox.requests),
        )

    def _clock(self) -> datetime:
        return self.now


def review_candidate(
    run: E2ERun,
    *,
    sandbox: SandboxExecutor,
    work_root: Path,
    now: datetime,
    review_name: str | None = None,
    git: GitRunner | None = None,
) -> tuple[SpecificationLock, SentinelVerdict]:
    """Review one PatchForge E2E run independently; used by benchmarks and live runs."""

    git = git or GitRunner(work_root / "git-sentinel")
    source = run.root / "source"
    profile = run.scenario.profile
    lock = capture_specification_lock(git, source, run.source_sha, profile, captured_at=now)
    name = review_name or run.scenario.name
    verifier = SentinelQAVerifier(
        task=run.task,
        profile=profile,
        lock=lock,
        artifact_store=run.artifacts,
        sandbox=sandbox,
        git=git,
        clock=lambda: now,
        review_id=uuid5(_SENTINEL_NAMESPACE, f"{name}:review"),
        work_root=work_root / "review",
    )
    return lock, verifier.review(run.result, source)


def spec_plan(
    profile: RepositoryProfile,
    purpose: CommandPurpose,
    status: SandboxStatus,
    *,
    summary: str | None = None,
) -> FakeSandboxPlan:
    """A scripted SentinelQA execution of one operator command with a pytest summary line."""

    command_sha256 = canonical_sha256(profile.commands[purpose])
    if status is SandboxStatus.SUCCEEDED:
        return FakeSandboxPlan(
            expected_command_sha256=command_sha256,
            status=status,
            exit_code=0,
            stdout=(summary if summary is not None else "1 passed in 0.01s").encode() + b"\n",
        )
    if status is SandboxStatus.FAILED:
        return FakeSandboxPlan(
            expected_command_sha256=command_sha256,
            status=status,
            exit_code=1,
            stdout=(summary if summary is not None else "1 failed in 0.01s").encode() + b"\n",
            stderr=b"AssertionError\n",
            error_code="command_failed",
        )
    return FakeSandboxPlan(
        expected_command_sha256=command_sha256, status=status, error_code="sandbox_error"
    )


def _source_refs(git: GitRunner, source: Path) -> str:
    return git.run(["-C", str(source), "show-ref", "--head"]).stdout_text()


def _counter(name: str, purpose: str) -> Callable[[], UUID]:
    count = 0

    def next_id() -> UUID:
        nonlocal count
        count += 1
        return uuid5(_SENTINEL_NAMESPACE, f"{name}:{purpose}:{count}")

    return next_id


def with_lock(context: SentinelContext, lock: SpecificationLock) -> SentinelContext:
    return replace(context, lock=lock)


__all__ = [
    "MAX_SPECIFICATION_RUNS",
    "SentinelContext",
    "SentinelQAHarness",
    "SentinelRun",
    "SentinelScenario",
    "Tamper",
    "review_candidate",
    "spec_plan",
    "with_lock",
]
