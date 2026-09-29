"""The resident engineer's executor: PatchForge produces, SentinelQA verifies, gates attest.

``PatchForgeExecutor`` never edits files itself. For a candidate whose category has a
mechanical recipe it provisions a disposable PatchForge workspace from the operator
checkout, runs the recipe's fixed script through the real ToolGateway, Runtime, and
Attestor, has SentinelQA verify the attested patch against the pristine specification,
maps the runtime-attested checks and the verdict to gate results, and materializes the
candidate as a commit on a local branch in a separate clone. It cannot ship: publishing a
branch or fast-forwarding ``main`` needs a publisher the owner has not configured, so the
policy turns every validated change into an approval request.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

from nexus.atlas.models import SourceRevision
from nexus.patchforge.attestor import LocalArtifactStore, PatchForgeAttestor
from nexus.patchforge.canonical import canonical_json, canonical_sha256
from nexus.patchforge.e2e import ScriptedEngine
from nexus.patchforge.gateway import ToolGateway
from nexus.patchforge.models import (
    CheckKind,
    CheckStatus,
    EngineeringTask,
    PatchOutcome,
    PatchResult,
    ReproductionStatus,
    RunIdentity,
    ToolName,
)
from nexus.patchforge.policy import PatchForgePolicy
from nexus.patchforge.runtime import PatchForgeRuntime
from nexus.patchforge.sandbox import SandboxExecutor
from nexus.patchforge.workspace import GitCommandError, GitRunner, WorkspaceError, WorkspaceManager
from nexus.sentinelqa.lock import SpecificationLockError, capture_specification_lock
from nexus.sentinelqa.verifier import SentinelQAVerifier
from nexus.software_engineer.cycle import ExecutionOutcome, ExecutorError
from nexus.software_engineer.models import (
    ChangeSummary,
    CycleBudget,
    EngineeringCandidate,
    GateResult,
    GateStatus,
    RollbackRecord,
    ValidationGate,
)
from nexus.software_engineer.recipes import (
    RECIPE_FOR_CATEGORY,
    Recipe,
    RecipeCommands,
    recipe_budgets,
    recipe_commands,
    recipe_profile,
    recipe_steps,
)

_EXECUTOR_NAMESPACE = UUID("f4a5b6c7-d8e9-4f0a-b1c2-d3e4f5a6b7c8")
BRANCH_PREFIX = "nexus/software-engineer"
COMMIT_AUTHOR_NAME = "NEXUS Resident Engineer"
COMMIT_AUTHOR_EMAIL = "resident-engineer@nexus.invalid"
_GATE_FOR_CHECK = {
    CheckKind.TARGETED_TESTS: ValidationGate.PYTEST_TARGETED,
    CheckKind.FULL_TEST_SUITE: ValidationGate.PYTEST_FULL,
    CheckKind.FORMATTER: ValidationGate.RUFF_FORMAT,
    CheckKind.LINTER: ValidationGate.RUFF_LINT,
    CheckKind.TYPECHECK: ValidationGate.MYPY,
}
_GATE_STATUS = {
    CheckStatus.PASSED: GateStatus.PASSED,
    CheckStatus.FAILED: GateStatus.FAILED,
    CheckStatus.ERROR: GateStatus.ERROR,
    CheckStatus.NOT_RUN: GateStatus.NOT_RUN,
}
_ALL_GATES = (
    ValidationGate.RUFF_FORMAT,
    ValidationGate.RUFF_LINT,
    ValidationGate.MYPY,
    ValidationGate.PYTEST_TARGETED,
    ValidationGate.PYTEST_FULL,
)


class PatchForgeExecutor:
    """Produce validated mechanical changes through PatchForge and SentinelQA."""

    can_ship = False

    def __init__(
        self,
        *,
        repo_root: Path,
        repository_url: str,
        sandbox: SandboxExecutor,
        run_root: Path,
        clock: Callable[[], datetime] | None = None,
        git: GitRunner | None = None,
        commands: Callable[[Recipe], RecipeCommands] | None = None,
        lease_duration: timedelta = timedelta(hours=2),
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.repository_url = repository_url
        self.sandbox = sandbox
        self.run_root = run_root
        self.clock = clock or (lambda: datetime.now(UTC))
        self.git = git or GitRunner(run_root / "git-runtime")
        self.commands = commands or recipe_commands
        self.lease_duration = lease_duration

    # -- CandidateExecutor -----------------------------------------------------------------

    def execute(
        self, candidate: EngineeringCandidate, *, cycle_id: UUID, budget: CycleBudget
    ) -> ExecutionOutcome:
        recipe = RECIPE_FOR_CATEGORY.get(candidate.category)
        if recipe is None:
            return ExecutionOutcome(
                change=None,
                patch=None,
                gates=_not_run(f"no mechanical recipe for {candidate.category.value}"),
                notes=(
                    f"Category {candidate.category.value} has no mechanical recipe; the "
                    "candidate needs investigation before any change.",
                ),
            )
        root = self.run_root / str(cycle_id) / candidate.candidate_id.hex[:12]
        root.mkdir(parents=True, exist_ok=True)
        now = self._now()
        try:
            head = self._git_text(["rev-parse", "HEAD"], cwd=self.repo_root)
        except GitCommandError as exc:
            raise ExecutorError("The operator checkout has no readable HEAD") from exc
        profile = recipe_profile(
            recipe,
            repository_url=self.repository_url,
            profile_id=f"software-engineer.{recipe.value.replace('_', '-')}",
            commands=self.commands(recipe),
        )
        profile_sha = canonical_sha256(profile)
        key = f"{cycle_id}:{candidate.candidate_id}"
        task = EngineeringTask(
            task_id=uuid5(_EXECUTOR_NAMESPACE, f"{key}:task"),
            atlas_job_id=uuid5(_EXECUTOR_NAMESPACE, f"{key}:job"),
            title=candidate.title,
            instructions=candidate.rationale,
            acceptance_criteria=[
                "The recipe's reproduction check passes on the final tree.",
                "The full test suite, linter, and type checker pass on the final tree.",
            ],
            source=SourceRevision(repository_url=profile.repository_url, commit_sha=head),
            repository_profile_id=profile.profile_id,
            repository_profile_sha256=profile_sha,
            created_at=now,
        )
        identity = RunIdentity(
            run_id=uuid5(_EXECUTOR_NAMESPACE, f"{key}:run"),
            task_id=task.task_id,
            atlas_job_id=task.atlas_job_id,
            atlas_execution_id=uuid5(_EXECUTOR_NAMESPACE, f"{key}:execution"),
            agent_id="patchforge.engineer",
            source=task.source,
            repository_profile_id=profile.profile_id,
            repository_profile_sha256=profile_sha,
            task_sha256=canonical_sha256(task),
            engine_kind="scripted",
            engine_version=f"software-engineer-{recipe.value}-v1",
            created_at=now,
        )
        policy = PatchForgePolicy(
            repository_profile_id=profile.profile_id,
            repository_profile_sha256=profile_sha,
            allowed_tools=list(ToolName),
            budgets=recipe_budgets(),
            max_changed_files=200,
            max_diff_bytes=2_000_000,
            allow_test_file_changes=False,
        )
        manager = WorkspaceManager(root / "workspaces", clock=self.clock, git_runner=self.git)
        try:
            handle = manager.provision(
                task, profile, identity.run_id, self.repo_root, lease_duration=self.lease_duration
            )
        except WorkspaceError as exc:
            raise ExecutorError(f"Workspace provisioning failed: {type(exc).__name__}") from exc
        gateway = ToolGateway(
            identity=identity,
            task=task,
            profile=profile,
            policy=policy,
            workspace=handle,
            workspace_manager=manager,
            sandbox=self.sandbox,
            clock=self.clock,
        )
        runtime = PatchForgeRuntime(
            gateway=gateway,
            workspace_manager=manager,
            engine=ScriptedEngine(recipe_steps(recipe), handle.worktree),
            lease_duration=self.lease_duration,
            clock=self.clock,
        )
        completion = runtime.execute()
        artifacts = LocalArtifactStore(root / "artifacts")
        result = PatchForgeAttestor(
            task=task, profile=profile, policy=policy, artifact_store=artifacts
        ).attest(completion)
        _write(root / "patch_result.json", canonical_json(result))
        gates = _gates_from_result(result)
        notes = [f"PatchForge outcome {result.outcome.value}"]
        if result.failure is not None:
            notes.append(f"PatchForge failure {result.failure.value}")
        notes.extend(
            f"{item.severity.value} {item.code}: {item.detail}" for item in result.policy_findings
        )
        tool_calls = len(result.tool_calls)
        if result.outcome is not PatchOutcome.PATCH_PROPOSED or result.diff is None:
            gates.append(
                GateResult(
                    gate=ValidationGate.SENTINEL_REVIEW,
                    status=GateStatus.NOT_RUN,
                    summary="No proposal to verify.",
                )
            )
            return ExecutionOutcome(
                change=None, patch=None, gates=gates, notes=tuple(notes), tool_calls=tool_calls
            )
        patch = artifacts.read(result.diff.patch_artifact)
        _write(root / "candidate.patch", patch)
        try:
            lock = capture_specification_lock(
                self.git, self.repo_root, head, profile, captured_at=now
            )
        except (SpecificationLockError, GitCommandError) as exc:
            raise ExecutorError(f"Specification lock capture failed: {type(exc).__name__}") from exc
        verifier = SentinelQAVerifier(
            task=task,
            profile=profile,
            lock=lock,
            artifact_store=artifacts,
            sandbox=self.sandbox,
            git=self.git,
            clock=self.clock,
            review_id=uuid5(_EXECUTOR_NAMESPACE, f"{key}:review"),
            work_root=root / "sentinelqa",
        )
        verdict = verifier.review(result, self.repo_root)
        _write(root / "sentinel_verdict.json", canonical_json(verdict))
        gates.append(
            GateResult(
                gate=ValidationGate.SENTINEL_REVIEW,
                status={
                    "passed": GateStatus.PASSED,
                    "failed": GateStatus.FAILED,
                    "inconclusive": GateStatus.ERROR,
                }[verdict.verdict.value],
                summary=verdict.summary[:500],
                evidence_sha256=verdict.verdict_sha256,
            )
        )
        tool_calls += len(verdict.runs)
        notes.append(f"SentinelQA {verdict.verdict.value}: {verdict.summary}")
        branch, commit = self._materialize_branch(root / "branch", cycle_id, patch, task)
        change = ChangeSummary(
            base_sha=head,
            branch=branch,
            commit_sha=commit,
            changed_files=list(result.diff.changed_files),
            additions=result.diff.additions,
            deletions=result.diff.deletions,
            diff_bytes=len(patch),
            diff_sha256=result.diff.diff_sha256,
            patch_result_sha256=canonical_sha256(result),
            sentinel_verdict_sha256=verdict.verdict_sha256,
            rollback_reference=(
                f"Nothing is published. Discard branch {branch} (commit {commit[:12]}); "
                f"the patch is at {root / 'candidate.patch'}"
            )[:500],
        )
        return ExecutionOutcome(
            change=change,
            patch=patch,
            gates=gates,
            notes=tuple(notes),
            root_cause_evidence=(
                result.reproduction is not None
                and result.reproduction.status is ReproductionStatus.FAIL_BEFORE_PASS_AFTER
            ),
            tool_calls=tool_calls,
        )

    def ship(self, change: ChangeSummary, *, cycle_id: UUID) -> ChangeSummary:
        raise ExecutorError(
            "Shipping is not configured: no publisher can push a branch or fast-forward main"
        )

    def rollback(self, change: ChangeSummary, *, reason: str) -> RollbackRecord:
        return RollbackRecord(
            reverted_commit_sha=change.commit_sha or change.base_sha,
            revert_reference=(
                f"Local branch {change.branch or 'unknown'} discarded; nothing was published."
            ),
            reason=reason[:500],
            occurred_at=self._now(),
        )

    # -- helpers -------------------------------------------------------------------------

    def _materialize_branch(
        self, target: Path, cycle_id: UUID, patch: bytes, task: EngineeringTask
    ) -> tuple[str, str]:
        branch = f"{BRANCH_PREFIX}/{cycle_id.hex[:12]}"
        try:
            self.git.run(
                ["clone", "--quiet", "--no-hardlinks", "--", str(self.repo_root), str(target)]
            )
            location = [
                "-C",
                str(target),
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.hooksPath=/dev/null",
            ]
            self.git.run([*location, "checkout", "--quiet", "-b", branch, task.source.commit_sha])
            patch_file = target.parent / "branch.patch"
            patch_file.write_bytes(patch)
            self.git.run(
                [*location, "apply", "--binary", "--whitespace=nowarn", "--", str(patch_file)]
            )
            patch_file.unlink()
            self.git.run([*location, "add", "--all", "--", "."])
            timestamp = f"@{int(self._now().timestamp())} +0000"
            self.git.run(
                [
                    *location,
                    "commit",
                    "--quiet",
                    "--no-verify",
                    "-m",
                    f"{task.title}\n\nProduced by the NEXUS resident engineer for task "
                    f"{task.task_id}.\nMechanical recipe; validated by PatchForge gates and "
                    "SentinelQA.",
                ],
                environment_overrides={
                    "GIT_AUTHOR_NAME": COMMIT_AUTHOR_NAME,
                    "GIT_AUTHOR_EMAIL": COMMIT_AUTHOR_EMAIL,
                    "GIT_AUTHOR_DATE": timestamp,
                    "GIT_COMMITTER_NAME": COMMIT_AUTHOR_NAME,
                    "GIT_COMMITTER_EMAIL": COMMIT_AUTHOR_EMAIL,
                    "GIT_COMMITTER_DATE": timestamp,
                },
            )
            commit = self._git_text(["rev-parse", "HEAD"], cwd=target)
        except (GitCommandError, OSError) as exc:
            raise ExecutorError(
                f"Candidate branch could not be created: {type(exc).__name__}"
            ) from exc
        return branch, commit

    def _git_text(self, arguments: list[str], *, cwd: Path) -> str:
        return self.git.run(["-C", str(cwd), *arguments]).stdout_text().strip()

    def _now(self) -> datetime:
        value = self.clock()
        if value.utcoffset() is None:
            raise ExecutorError("Executor clock must be timezone-aware")
        return value


def _gates_from_result(result: PatchResult) -> list[GateResult]:
    by_gate: dict[ValidationGate, GateResult] = {}
    for check in result.checks:
        gate = _GATE_FOR_CHECK[check.check_kind]
        status = _GATE_STATUS[check.status]
        by_gate[gate] = GateResult(
            gate=gate,
            status=status,
            summary=check.summary[:500],
            evidence_sha256=canonical_sha256(check) if status is not GateStatus.NOT_RUN else None,
        )
    for gate in _ALL_GATES:
        by_gate.setdefault(
            gate,
            GateResult(gate=gate, status=GateStatus.NOT_RUN, summary="PatchForge did not run it."),
        )
    return [by_gate[gate] for gate in _ALL_GATES]


def _not_run(reason: str) -> list[GateResult]:
    return [
        GateResult(gate=gate, status=GateStatus.NOT_RUN, summary=reason[:500])
        for gate in (*_ALL_GATES, ValidationGate.SENTINEL_REVIEW)
    ]


def _write(target: Path, payload: str | bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    if isinstance(payload, bytes):
        temporary.write_bytes(payload)
    else:
        temporary.write_text(payload + "\n", encoding="utf-8")
    os.replace(temporary, target)


__all__ = ["BRANCH_PREFIX", "PatchForgeExecutor"]
