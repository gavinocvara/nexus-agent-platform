"""Independent verification of one PatchForge candidate against the pristine specification.

``SentinelQAVerifier.review`` never trusts PatchForge's conclusions. It:

1. checks that the candidate, task, operator profile, and specification lock bind to the
   same source commit;
2. reads the patch from the content-addressed artifact store and checks its hash;
3. materializes two fresh trees of the exact source commit from Git objects, outside any
   PatchForge workspace, and proves the pristine tree matches the lock;
4. runs the operator's commands on the pristine tree for a baseline;
5. applies the patch to the second tree and compares every locked file with the lock;
6. builds the *verification tree*: the candidate with every locked file restored to its
   pristine content and every candidate-added test or configuration file removed;
7. runs the operator's targeted and full-suite commands on that tree;
8. cross-checks the outcome with PatchForge's attested checks.

Any evidence it cannot establish makes the verdict ``inconclusive``; any evidence against
the candidate makes it ``failed``. Only a clean review is ``passed``.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid5

from nexus.atlas.models import ArtifactReference, ReviewVerdict
from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.gateway import is_sensitive_path, path_matches
from nexus.patchforge.models import (
    CheckKind,
    CheckStatus,
    DiffSummary,
    EngineeringTask,
    ExecutionStatus,
    FindingSeverity,
    PatchOutcome,
    PatchResult,
)
from nexus.patchforge.policy import CommandPurpose, RepositoryProfile
from nexus.patchforge.sandbox import SandboxExecutor
from nexus.patchforge.workspace import (
    GitCommandError,
    GitCommandResult,
    GitRunner,
    WorkspaceError,
)
from nexus.sentinelqa.canary import RunnerCanary, runner_canary
from nexus.sentinelqa.executor import SpecificationExecutionError, SpecificationRunner
from nexus.sentinelqa.lock import (
    SpecificationLockError,
    capture_specification_lock,
    compare_tree_with_lock,
    inspect_file,
    specification_files_in_tree,
)
from nexus.sentinelqa.models import (
    FINDING_CATEGORY,
    FindingCategory,
    SentinelFinding,
    SentinelFindingCode,
    SentinelVerdict,
    SpecificationIntegrity,
    SpecificationKind,
    SpecificationLock,
    SpecificationRun,
    SpecificationTree,
    is_evaluation_config_path,
    verdict_for,
)
from nexus.sentinelqa.tamper import harness_tampering_lines

_FINDING_NAMESPACE = UUID("7a1d4c2b-3e5f-4a6b-8c9d-0e1f2a3b4c5e")
_VERIFICATION_PURPOSES = (CommandPurpose.TARGETED_TESTS, CommandPurpose.FULL_TEST_SUITE)
_CHECK_FOR_PURPOSE = {
    CommandPurpose.TARGETED_TESTS: CheckKind.TARGETED_TESTS,
    CommandPurpose.FULL_TEST_SUITE: CheckKind.FULL_TEST_SUITE,
}
MAX_PATHS_PER_FINDING = 20


class ArtifactReader(Protocol):
    def read(self, reference: ArtifactReference) -> bytes: ...


class SentinelReferenceError(RuntimeError):
    """The pristine source commit cannot be materialized faithfully."""


@dataclass(frozen=True, slots=True)
class _Finding:
    code: SentinelFindingCode
    detail: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class _Tree:
    git_directory: Path
    worktree: Path


class _Stop(Exception):
    """Verification cannot continue; the findings so far decide the verdict."""


class SentinelQAVerifier:
    """Review PatchForge candidates for one task, profile, and specification lock."""

    def __init__(
        self,
        *,
        task: EngineeringTask,
        profile: RepositoryProfile,
        lock: SpecificationLock,
        artifact_store: ArtifactReader,
        sandbox: SandboxExecutor,
        git: GitRunner,
        clock: Callable[[], datetime],
        review_id: UUID,
        work_root: Path,
        additional_specification_paths: Sequence[str] = (),
    ) -> None:
        self.task = task
        self.profile = profile
        self.lock = lock
        self.artifact_store = artifact_store
        self.sandbox = sandbox
        self.git = git
        self.clock = clock
        self.review_id = review_id
        self.work_root = work_root
        self.additional_paths = tuple(additional_specification_paths)

    def review(self, result: PatchResult, source_repository: Path) -> SentinelVerdict:
        started_at = self._now()
        findings: list[_Finding] = []
        runner = SpecificationRunner(
            review_id=self.review_id, profile=self.profile, sandbox=self.sandbox, clock=self.clock
        )
        integrity: SpecificationIntegrity | None = None
        agree: bool | None = None
        diff = result.diff
        try:
            if result.outcome is not PatchOutcome.PATCH_PROPOSED or diff is None:
                raise self._stop(
                    findings,
                    SentinelFindingCode.NOT_A_PROPOSAL,
                    f"PatchForge outcome {result.outcome.value} proposes no patch to verify.",
                )
            self._check_identity(result, diff, findings)
            self._stop_if_unverifiable(findings)
            patch = self._read_patch(diff, findings)
            trees = self._materialize_trees(source_repository, diff.base_sha, findings)
            self._verify_pristine_reference(trees["pristine"], findings)
            baseline = self._baseline_runs(runner, trees["pristine"], findings)
            candidate = trees["candidate"]
            self._apply_patch(candidate, patch, findings)
            changed = self._changed_files(candidate)
            self._check_recorded_changes(diff, changed, findings)
            integrity, restore, remove = self._inspect_integrity(candidate, changed, findings)
            self._check_harness_tampering(patch, findings)
            integrity = self._build_verification_tree(
                candidate, trees["pristine"], integrity, restore, remove, findings
            )
            if not integrity.verification_changed_files:
                raise self._stop(
                    findings,
                    SentinelFindingCode.NO_CODE_CHANGE,
                    "Once the pristine specification is restored, the candidate changes nothing.",
                )
            verification = self._verification_runs(runner, candidate, baseline, findings)
            agree = self._cross_check(result, verification, integrity, findings)
            if not any(
                FINDING_CATEGORY[item.code] is not FindingCategory.ADVISORY for item in findings
            ):
                self._check_runner_integrity(runner, candidate, trees["pristine"], findings)
        except _Stop:
            pass
        return self._verdict(result, diff, findings, integrity, runner.runs, agree, started_at)

    # -- stages -------------------------------------------------------------------------

    def _check_identity(
        self, result: PatchResult, diff: DiffSummary, findings: list[_Finding]
    ) -> None:
        task = self.task
        lock = self.lock
        identity = result.identity
        problems: list[str] = []
        if identity.task_id != task.task_id or identity.task_sha256 != canonical_sha256(task):
            problems.append("the PatchForge run belongs to another task")
        if canonical_sha256(self.profile) != task.repository_profile_sha256:
            problems.append("the operator profile does not match the task binding")
        if (
            lock.profile_sha256 != task.repository_profile_sha256
            or lock.profile_id != task.repository_profile_id
        ):
            problems.append("the specification lock was captured under another profile")
        if lock.test_path_prefixes != list(self.profile.test_path_prefixes):
            problems.append("the specification lock names different test prefixes")
        if str(lock.repository_url) != str(task.source.repository_url):
            problems.append("the specification lock names another repository")
        if lock.source_sha != task.source.commit_sha:
            problems.append("the specification lock was captured at another source commit")
        if diff.base_sha != task.source.commit_sha or identity.source != task.source:
            problems.append("the candidate is not rooted at the task's source commit")
        for problem in problems:
            findings.append(
                _Finding(SentinelFindingCode.IDENTITY_MISMATCH, problem.capitalize() + ".")
            )

    def _read_patch(self, diff: DiffSummary, findings: list[_Finding]) -> bytes:
        try:
            patch = self.artifact_store.read(diff.patch_artifact)
        except Exception as exc:
            raise self._stop(
                findings,
                SentinelFindingCode.EVIDENCE_INCOMPLETE,
                f"The patch artifact cannot be read: {type(exc).__name__}.",
            ) from exc
        digest = sha256(patch).hexdigest()
        if digest != diff.diff_sha256 or digest != diff.patch_artifact.sha256:
            raise self._stop(
                findings,
                SentinelFindingCode.EVIDENCE_INCOMPLETE,
                "The patch artifact does not match the attested diff hash.",
            )
        if not patch:
            raise self._stop(
                findings, SentinelFindingCode.EVIDENCE_INCOMPLETE, "The patch artifact is empty."
            )
        return patch

    def _materialize_trees(
        self, source: Path, base_sha: str, findings: list[_Finding]
    ) -> dict[str, _Tree]:
        trees: dict[str, _Tree] = {}
        for name in ("pristine", "candidate"):
            try:
                trees[name] = self._materialize(source, base_sha, self.work_root / name)
            except (SentinelReferenceError, WorkspaceError, OSError) as exc:
                raise self._stop(
                    findings,
                    SentinelFindingCode.PRISTINE_REFERENCE_UNVERIFIABLE,
                    f"The source commit could not be materialized: {_short(exc)}",
                ) from exc
        return trees

    def _materialize(self, source: Path, base_sha: str, root: Path) -> _Tree:
        root.mkdir(parents=True)
        git_directory = root / "control.git"
        worktree = root / "worktree"
        worktree.mkdir()
        self.git.run(
            [
                "clone",
                "--bare",
                "--no-hardlinks",
                "--no-tags",
                "--",
                str(source),
                str(git_directory),
            ]
        )
        tree = _Tree(git_directory=git_directory, worktree=worktree)
        self._git(tree, ["rev-parse", "--verify", f"{base_sha}^{{commit}}"])
        self._git(tree, ["checkout", "--force", "--detach", base_sha, "--"])
        head = self._git(tree, ["rev-parse", "HEAD"]).stdout_text().strip()
        if head != base_sha:
            raise SentinelReferenceError("Materialized tree is not at the source commit")
        if (worktree / ".git").exists():
            raise SentinelReferenceError("Materialized worktree contains Git metadata")
        return tree

    def _verify_pristine_reference(self, tree: _Tree, findings: list[_Finding]) -> None:
        modified, deleted, not_regular = compare_tree_with_lock(tree.worktree, self.lock)
        problems = [*modified, *deleted, *not_regular]
        try:
            recaptured = capture_specification_lock(
                self.git,
                tree.git_directory,
                self.lock.source_sha,
                self.profile,
                captured_at=self.lock.captured_at,
                additional_paths=self.additional_paths,
            )
        except (SpecificationLockError, WorkspaceError) as exc:
            raise self._stop(
                findings,
                SentinelFindingCode.PRISTINE_REFERENCE_UNVERIFIABLE,
                f"The specification could not be re-derived from the source commit: {_short(exc)}",
            ) from exc
        if recaptured.entries != self.lock.entries:
            problems.append("<lock entries differ from the source commit>")
        if problems:
            raise self._stop(
                findings,
                SentinelFindingCode.PRISTINE_REFERENCE_UNVERIFIABLE,
                "The pristine source tree does not match the specification lock "
                f"({len(problems)} difference(s)).",
                problems[0] if not problems[0].startswith("<") else None,
            )

    def _baseline_runs(
        self, runner: SpecificationRunner, tree: _Tree, findings: list[_Finding]
    ) -> dict[CommandPurpose, SpecificationRun]:
        baseline: dict[CommandPurpose, SpecificationRun] = {}
        purposes = [
            purpose
            for purpose in (CommandPurpose.REPRODUCTION, *_VERIFICATION_PURPOSES)
            if purpose in self.profile.commands
        ]
        if not set(_VERIFICATION_PURPOSES).issubset(purposes):
            raise self._stop(
                findings,
                SentinelFindingCode.EVIDENCE_INCOMPLETE,
                "The operator profile lacks a targeted or full-suite command to verify with.",
            )
        for purpose in purposes:
            run = self._run(runner, SpecificationTree.PRISTINE, tree.worktree, purpose, findings)
            if not run.tree_unchanged:
                raise self._stop(
                    findings,
                    SentinelFindingCode.EVIDENCE_INCOMPLETE,
                    f"The pristine {purpose.value} run changed the tree it ran on.",
                )
            baseline[purpose] = run
            if purpose is CommandPurpose.REPRODUCTION and run.status is ExecutionStatus.PASSED:
                findings.append(
                    _Finding(
                        SentinelFindingCode.REPRODUCTION_NOT_DEMONSTRATED,
                        "The reproduction command already passes on the pristine source tree.",
                    )
                )
        return baseline

    def _run(
        self,
        runner: SpecificationRunner,
        tree: SpecificationTree,
        root: Path,
        purpose: CommandPurpose,
        findings: list[_Finding],
    ) -> SpecificationRun:
        try:
            run = runner.run(tree, root, purpose)
        except SpecificationExecutionError as exc:
            raise self._stop(
                findings,
                SentinelFindingCode.EXECUTOR_UNAVAILABLE,
                f"The {purpose.value} command could not be executed: {_short(exc)}",
            ) from exc
        if run.status not in {ExecutionStatus.PASSED, ExecutionStatus.FAILED}:
            raise self._stop(
                findings,
                SentinelFindingCode.EVIDENCE_INCOMPLETE,
                f"The {tree.value} {purpose.value} run ended with {run.status.value}.",
            )
        if run.counts is None and purpose in _VERIFICATION_PURPOSES:
            raise self._stop(
                findings,
                SentinelFindingCode.EVIDENCE_INCOMPLETE,
                f"The {tree.value} {purpose.value} run produced no parseable test summary.",
            )
        if (
            run.counts is not None
            and run.status is ExecutionStatus.PASSED
            and (run.counts.failed or run.counts.errors)
        ):
            raise self._stop(
                findings,
                SentinelFindingCode.EVIDENCE_INCOMPLETE,
                f"The {tree.value} {purpose.value} run exited successfully although its "
                "summary reports failures.",
            )
        return run

    def _apply_patch(self, tree: _Tree, patch: bytes, findings: list[_Finding]) -> None:
        patch_file = self.work_root / "candidate.patch"
        patch_file.write_bytes(patch)
        try:
            self._git(tree, ["apply", "--binary", "--whitespace=nowarn", "--", str(patch_file)])
        except GitCommandError as exc:
            raise self._stop(
                findings,
                SentinelFindingCode.PATCH_APPLY_FAILED,
                f"The patch does not apply to its own base commit: {_short(exc)}",
            ) from exc

    def _changed_files(self, tree: _Tree) -> set[str]:
        """Paths that differ from the source commit, computed as PatchForge computes them."""

        self._git(tree, ["read-tree", "HEAD"])
        self._git(tree, ["add", "--intent-to-add", "--", "."])
        names = self._git(tree, ["diff", "--name-only", "-z", "HEAD", "--"]).stdout
        return {item.decode("utf-8", errors="strict") for item in names.split(b"\0") if item}

    def _check_recorded_changes(
        self, diff: DiffSummary, changed: set[str], findings: list[_Finding]
    ) -> None:
        recorded = set(diff.changed_files)
        for path in sorted(recorded - changed)[:MAX_PATHS_PER_FINDING]:
            findings.append(
                _Finding(
                    SentinelFindingCode.EVIDENCE_INCOMPLETE,
                    "The attested diff lists a change the applied patch does not make.",
                    path,
                )
            )
        for path in sorted(changed - recorded)[:MAX_PATHS_PER_FINDING]:
            findings.append(
                _Finding(
                    SentinelFindingCode.UNRECORDED_CHANGE,
                    "The applied patch changes a path the attested diff does not list.",
                    path,
                )
            )
        self._stop_if_unverifiable(findings)

    def _check_harness_tampering(self, patch: bytes, findings: list[_Finding]) -> None:
        """Code under test that reaches into the test harness redefines correctness."""

        def is_specification(path: str) -> bool:
            return is_evaluation_config_path(path) or path_matches(
                path, self.profile.test_path_prefixes
            )

        for path, detail in harness_tampering_lines(patch, is_specification=is_specification):
            findings.append(
                _Finding(
                    SentinelFindingCode.HARNESS_TAMPERING,
                    f"The candidate's code reaches into the test harness ({detail}).",
                    path,
                )
            )

    def _inspect_integrity(
        self, tree: _Tree, changed: set[str], findings: list[_Finding]
    ) -> tuple[SpecificationIntegrity, list[str], list[str]]:
        lock = self.lock
        by_path = lock.by_path
        modified, deleted, not_regular = compare_tree_with_lock(tree.worktree, lock)
        present = specification_files_in_tree(tree.worktree, self.profile, self.additional_paths)
        added = sorted(set(present) - set(by_path))
        added_tests = [path for path in added if present[path] is SpecificationKind.TEST]
        added_other = [path for path in added if present[path] is not SpecificationKind.TEST]
        config_changes = sorted(
            {
                *(path for path in changed if is_evaluation_config_path(path)),
                *(
                    path
                    for path in (*modified, *deleted, *not_regular)
                    if by_path[path].kind is SpecificationKind.EVALUATION_CONFIG
                ),
                *added_other,
            }
        )
        self._add_paths(
            findings,
            SentinelFindingCode.SPECIFICATION_MODIFIED,
            [path for path in modified if path not in config_changes],
            "The candidate changes a locked specification file.",
        )
        self._add_paths(
            findings,
            SentinelFindingCode.SPECIFICATION_DELETED,
            [path for path in deleted if path not in config_changes],
            "The candidate deletes a locked specification file.",
        )
        self._add_paths(
            findings,
            SentinelFindingCode.SPECIFICATION_NOT_REGULAR_FILE,
            [path for path in not_regular if path not in config_changes],
            "The candidate replaces a locked specification file with a non-regular file.",
        )
        self._add_paths(
            findings,
            SentinelFindingCode.EVALUATION_CONFIG_ALTERED,
            config_changes,
            "The candidate adds, changes, or removes evaluation configuration.",
        )
        self._add_paths(
            findings,
            SentinelFindingCode.PROTECTED_PATH_CHANGED,
            sorted(path for path in changed if path_matches(path, self.profile.protected_paths)),
            "The candidate changes an operator-protected path.",
        )
        self._add_paths(
            findings,
            SentinelFindingCode.SENSITIVE_PATH_CHANGED,
            sorted(path for path in changed if is_sensitive_path(path)),
            "The candidate changes a sensitive path.",
        )
        if added_tests:
            findings.append(
                _Finding(
                    SentinelFindingCode.CANDIDATE_TESTS_EXCLUDED,
                    f"{len(added_tests)} candidate-added test file(s) are excluded from "
                    "verification; only the pristine specification is evidence.",
                    added_tests[0],
                )
            )
        integrity = SpecificationIntegrity(
            locked_entries=len(lock.entries),
            unchanged=len(lock.entries) - len(modified) - len(deleted) - len(not_regular),
            modified=modified[:500],
            deleted=deleted[:500],
            not_regular=not_regular[:500],
            added_tests_excluded=added_tests[:500],
            evaluation_config_changes=config_changes[:500],
            candidate_changed_files=sorted(changed)[:1000],
        )
        return integrity, sorted({*modified, *deleted, *not_regular}), added

    def _build_verification_tree(
        self,
        tree: _Tree,
        pristine: _Tree,
        integrity: SpecificationIntegrity,
        restore: list[str],
        remove: list[str],
        findings: list[_Finding],
    ) -> SpecificationIntegrity:
        by_path = self.lock.by_path
        try:
            for path in remove:
                _remove_path(tree.worktree, path)
            for path in restore:
                entry = by_path[path]
                _remove_path(tree.worktree, path)
                content = _read_pristine(pristine.worktree, path, entry.sha256)
                target = tree.worktree.joinpath(*path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                if entry.mode == "100755":
                    os.chmod(target, target.stat().st_mode | stat.S_IXUSR)
                else:
                    os.chmod(
                        target,
                        target.stat().st_mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH),
                    )
        except (OSError, SentinelReferenceError) as exc:
            raise self._stop(
                findings,
                SentinelFindingCode.EVIDENCE_INCOMPLETE,
                f"The verification tree could not be built: {_short(exc)}",
            ) from exc
        modified, deleted, not_regular = compare_tree_with_lock(tree.worktree, self.lock)
        present = specification_files_in_tree(tree.worktree, self.profile, self.additional_paths)
        if modified or deleted or not_regular or set(present) != set(by_path):
            raise self._stop(
                findings,
                SentinelFindingCode.EVIDENCE_INCOMPLETE,
                "The verification tree does not reproduce the pristine specification.",
            )
        remaining = sorted(self._changed_files(tree))
        return integrity.model_copy(
            update={
                "restored": restore[:500],
                "verification_changed_files": remaining[:1000],
            }
        )

    def _verification_runs(
        self,
        runner: SpecificationRunner,
        tree: _Tree,
        baseline: dict[CommandPurpose, SpecificationRun],
        findings: list[_Finding],
    ) -> dict[CommandPurpose, SpecificationRun]:
        verification: dict[CommandPurpose, SpecificationRun] = {}
        for purpose in _VERIFICATION_PURPOSES:
            run = self._run(
                runner, SpecificationTree.VERIFICATION, tree.worktree, purpose, findings
            )
            verification[purpose] = run
            counts = run.counts
            base = baseline[purpose].counts
            assert counts is not None and base is not None
            label = purpose.value
            if not run.tree_unchanged:
                findings.append(
                    _Finding(
                        SentinelFindingCode.VERIFICATION_TREE_MUTATED,
                        f"The {label} run changed the verification tree.",
                    )
                )
            if run.status is ExecutionStatus.FAILED or counts.failed or counts.errors:
                findings.append(
                    _Finding(
                        SentinelFindingCode.PRISTINE_TESTS_FAILED,
                        f"The pristine {label} fail on the candidate: {counts.failed} failed, "
                        f"{counts.errors} error(s), {counts.passed} passed.",
                    )
                )
            if counts.skipped > base.skipped or counts.xfailed > base.xfailed:
                findings.append(
                    _Finding(
                        SentinelFindingCode.TESTS_SKIPPED,
                        f"The {label} run skipped {counts.skipped} and xfailed {counts.xfailed} "
                        f"test(s); the pristine tree skipped {base.skipped} and xfailed "
                        f"{base.xfailed}.",
                    )
                )
            if (
                counts.total < base.total
                or counts.deselected > base.deselected
                or counts.total == 0
            ):
                findings.append(
                    _Finding(
                        SentinelFindingCode.TESTS_MISSING,
                        f"The {label} run accounted for {counts.total} test(s) with "
                        f"{counts.deselected} deselected; the pristine tree accounted for "
                        f"{base.total} with {base.deselected} deselected.",
                    )
                )
        return verification

    def _cross_check(
        self,
        result: PatchResult,
        verification: dict[CommandPurpose, SpecificationRun],
        integrity: SpecificationIntegrity,
        findings: list[_Finding],
    ) -> bool:
        checks = {item.check_kind: item for item in result.checks}
        executions = {item.execution_id: item for item in result.executions}
        agree = True
        for purpose, run in verification.items():
            check = checks.get(_CHECK_FOR_PURPOSE[purpose])
            if check is None or check.execution_id is None:
                continue
            execution = executions[check.execution_id]
            if execution.command_sha256 != run.command_sha256:
                findings.append(
                    _Finding(
                        SentinelFindingCode.IDENTITY_MISMATCH,
                        f"PatchForge ran a different {purpose.value} command than the profile.",
                    )
                )
                continue
            passed = run.status is ExecutionStatus.PASSED
            if check.status is CheckStatus.PASSED and not passed:
                agree = False
                reason = (
                    "the candidate passed only with its own changes to the specification"
                    if not integrity.specification_intact
                    else "PatchForge's passing evidence does not reproduce on a fresh tree"
                )
                findings.append(
                    _Finding(
                        SentinelFindingCode.INDEPENDENT_VALIDATION_DISAGREES,
                        f"PatchForge attested passing {purpose.value}, but the pristine "
                        f"specification fails independently: {reason}.",
                    )
                )
        return agree

    def _check_runner_integrity(
        self,
        runner: SpecificationRunner,
        candidate: _Tree,
        pristine: _Tree,
        findings: list[_Finding],
    ) -> None:
        """Require the runner to report a planted failure on the verification tree.

        The candidate's code shares the runner's process, so a clean summary alone does not
        prove that failures would be reported. Only a candidate that is about to pass pays
        for the probe; the pristine control runs only when the canary goes unreported, to
        tell a runner the candidate silenced from a profile that cannot collect a canary.
        """

        canary = runner_canary(self.review_id, self.lock)
        if canary is None:
            raise self._stop(
                findings,
                SentinelFindingCode.RUNNER_INTEGRITY_UNPROVEN,
                "The locked specification has no pytest module beside which a "
                "runner-integrity canary could be planted.",
            )
        probe = self._probe(runner, SpecificationTree.VERIFICATION, candidate, canary, findings)
        if probe.runner_integrity_demonstrated:
            return
        if not probe.tree_unchanged:
            findings.append(
                _Finding(
                    SentinelFindingCode.VERIFICATION_TREE_MUTATED,
                    "The runner-integrity run changed the verification tree.",
                )
            )
            return
        reported = probe.canary is not None and probe.canary.reported_failed
        counts = probe.counts
        if reported and counts is not None and (counts.failed > 1 or counts.errors):
            findings.append(
                _Finding(
                    SentinelFindingCode.PRISTINE_TESTS_FAILED,
                    "With the runner-integrity canary planted, the pristine full suite fails "
                    f"on the candidate: {counts.failed - 1} failed besides the canary, "
                    f"{counts.errors} error(s).",
                )
            )
            return
        control = self._probe(runner, SpecificationTree.PRISTINE, pristine, canary, findings)
        if not control.tree_unchanged:
            raise self._stop(
                findings,
                SentinelFindingCode.EVIDENCE_INCOMPLETE,
                "The pristine runner-integrity run changed the tree it ran on.",
            )
        if (
            control.canary is not None
            and control.canary.reported_failed
            and control.status is ExecutionStatus.FAILED
        ):
            findings.append(
                _Finding(
                    SentinelFindingCode.RUNNER_INTEGRITY_VIOLATED,
                    "The runner reports a planted failing test on the pristine tree but not on "
                    "the candidate: the candidate's code changes how test results are reported.",
                    canary.path,
                )
            )
            return
        raise self._stop(
            findings,
            SentinelFindingCode.RUNNER_INTEGRITY_UNPROVEN,
            "The full-suite command does not report a planted failing test even on the "
            "pristine tree, so this profile cannot demonstrate runner integrity.",
            canary.path,
        )

    def _probe(
        self,
        runner: SpecificationRunner,
        tree: SpecificationTree,
        root: _Tree,
        canary: RunnerCanary,
        findings: list[_Finding],
    ) -> SpecificationRun:
        purpose = CommandPurpose.FULL_TEST_SUITE
        try:
            run = runner.run(tree, root.worktree, purpose, canary=canary)
        except SpecificationExecutionError as exc:
            raise self._stop(
                findings,
                SentinelFindingCode.EXECUTOR_UNAVAILABLE,
                f"The runner-integrity run could not be executed: {_short(exc)}",
            ) from exc
        if run.status not in {ExecutionStatus.PASSED, ExecutionStatus.FAILED}:
            raise self._stop(
                findings,
                SentinelFindingCode.EVIDENCE_INCOMPLETE,
                f"The {tree.value} runner-integrity run ended with {run.status.value}.",
            )
        return run

    # -- verdict ------------------------------------------------------------------------

    def _verdict(
        self,
        result: PatchResult,
        diff: DiffSummary | None,
        findings: list[_Finding],
        integrity: SpecificationIntegrity | None,
        runs: list[SpecificationRun],
        agree: bool | None,
        started_at: datetime,
    ) -> SentinelVerdict:
        typed = [
            SentinelFinding(
                finding_id=uuid5(_FINDING_NAMESPACE, f"{self.review_id}:{index}:{item.code}"),
                code=item.code,
                category=FINDING_CATEGORY[item.code],
                severity=(
                    FindingSeverity.WARNING
                    if FINDING_CATEGORY[item.code] is FindingCategory.ADVISORY
                    else FindingSeverity.BLOCKING
                ),
                detail=item.detail[:500],
                path=item.path,
            )
            for index, item in enumerate(findings)
        ]
        verdict = verdict_for({item.category for item in typed})
        return SentinelVerdict(
            review_id=self.review_id,
            task_id=self.task.task_id,
            patchforge_run_id=result.identity.run_id,
            atlas_job_id=self.task.atlas_job_id,
            source_sha=self.task.source.commit_sha,
            lock_sha256=self.lock.lock_sha256,
            patch_sha256=diff.diff_sha256 if diff is not None else None,
            proposed_head_sha=diff.proposed_head_sha if diff is not None else None,
            verdict=verdict,
            summary=_summary(verdict, typed, integrity),
            findings=typed,
            integrity=integrity,
            runs=runs,
            patchforge_checks_agree=agree,
            started_at=started_at,
            completed_at=max(started_at, self._now()),
        )

    # -- helpers ------------------------------------------------------------------------

    @staticmethod
    def _stop(
        findings: list[_Finding], code: SentinelFindingCode, detail: str, path: str | None = None
    ) -> _Stop:
        findings.append(_Finding(code, detail, path))
        return _Stop()

    @staticmethod
    def _stop_if_unverifiable(findings: list[_Finding]) -> None:
        if any(FINDING_CATEGORY[item.code] is FindingCategory.UNVERIFIABLE for item in findings):
            raise _Stop()

    @staticmethod
    def _add_paths(
        findings: list[_Finding], code: SentinelFindingCode, paths: Sequence[str], detail: str
    ) -> None:
        for path in paths[:MAX_PATHS_PER_FINDING]:
            findings.append(_Finding(code, detail, path))
        if len(paths) > MAX_PATHS_PER_FINDING:
            findings.append(
                _Finding(code, f"{detail} ({len(paths) - MAX_PATHS_PER_FINDING} more path(s))")
            )

    def _git(self, tree: _Tree, arguments: Sequence[str]) -> GitCommandResult:
        return self.git.run(
            [
                f"--git-dir={tree.git_directory}",
                f"--work-tree={tree.worktree}",
                "-c",
                "core.hooksPath=NUL" if os.name == "nt" else "core.hooksPath=/dev/null",
                "-c",
                "core.autocrlf=false",
                *arguments,
            ],
            cwd=tree.worktree,
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.utcoffset() is None:
            raise ValueError("SentinelQA clock must be timezone-aware")
        return value


def _remove_path(root: Path, relative: str) -> None:
    target = root.joinpath(*relative.split("/"))
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        raise SentinelReferenceError(f"Specification path is a directory: {relative}")


def _read_pristine(root: Path, relative: str, expected_sha256: str) -> bytes:
    state = inspect_file(root, relative)
    if not state.regular or state.sha256 != expected_sha256:
        raise SentinelReferenceError(f"Pristine copy of {relative} is unavailable")
    return root.joinpath(*relative.split("/")).read_bytes()


def _summary(
    verdict: ReviewVerdict,
    findings: Sequence[SentinelFinding],
    integrity: SpecificationIntegrity | None,
) -> str:
    codes = sorted(
        {item.code.value for item in findings if item.category is not FindingCategory.ADVISORY}
    )
    if verdict is ReviewVerdict.PASSED:
        locked = integrity.locked_entries if integrity is not None else 0
        return (
            f"The candidate passes the pristine specification ({locked} locked file(s) "
            "unchanged; targeted and full-suite runs passed on a fresh tree, and the runner "
            "reported a planted failing test)."
        )
    if verdict is ReviewVerdict.FAILED:
        return "The candidate is rejected: " + ", ".join(codes) + "."
    return "The candidate could not be verified: " + ", ".join(codes) + "."


def _short(exc: BaseException) -> str:
    """Name the failure class only; messages may embed local paths or untrusted output."""

    return type(exc).__name__


__all__ = [
    "MAX_PATHS_PER_FINDING",
    "ArtifactReader",
    "SentinelQAVerifier",
    "SentinelReferenceError",
]
