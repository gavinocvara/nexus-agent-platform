"""Owner-run integration exercise for the GitHub draft-pull-request publisher.

The first real publication should not be a real change. This exercise publishes one
purpose-built, harmless file through the production publisher and every check it makes,
proves the refusals the publisher promises, and withdraws what it created. It runs only
when the owner invokes it with explicit confirmation and a token in the environment;
nothing in a cycle calls it.

It proves, against the live API: authentication and repository identity; the base commit;
local blob and tree integrity (the bundle is re-derived from the patch); remote blob,
tree, commit, reference, and pull-request integrity (the publisher's own checks); that a
moved base is refused before anything is written; that the draft is a draft, not merged,
without auto-merge; that a duplicate publication and an existing branch are refused; and
that withdrawal closes the pull request and deletes the branch. Every outcome is written
to an audit record without the token.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints

from nexus.atlas.models import ActorIdentity, ActorType, CommitSha, StrictModel
from nexus.patchforge.workspace import GitCommandError, GitRunner
from nexus.software_engineer.models import PublishedChange
from nexus.software_engineer.publish import (
    GitHubDraftPullRequestPublisher,
    PublishError,
    PublishSubmission,
    bundle_from_branch,
)

EXERCISE_BRANCH_PREFIX = "nexus/integration-exercise"
EXERCISE_DIRECTORY = "integration-exercise"
EXERCISE_AUTHOR_NAME = "NEXUS Integration Exercise"
EXERCISE_AUTHOR_EMAIL = "integration-exercise@nexus.invalid"
_GIT_OPTIONS = ("-c", "core.autocrlf=false", "-c", "core.hooksPath=/dev/null")
CheckName = Literal[
    "repository_identity",
    "base_available_locally",
    "bundle_rederived",
    "moved_base_refused",
    "draft_published",
    "draft_not_merged",
    "duplicate_refused",
    "withdrawn",
]
_CHECKS: tuple[CheckName, ...] = (
    "repository_identity",
    "base_available_locally",
    "bundle_rederived",
    "moved_base_refused",
    "draft_published",
    "draft_not_merged",
    "duplicate_refused",
    "withdrawn",
)


class ExerciseCheck(StrictModel):
    name: CheckName
    status: Literal["passed", "failed", "not_run"]
    detail: Annotated[str, StringConstraints(max_length=500)] = ""


class PublisherExerciseRecord(StrictModel):
    """Audit record of one exercise; runtime-attested, never carries the token."""

    schema_version: Literal[1] = 1
    exercise_id: UUID
    repository: Annotated[str, StringConstraints(min_length=3, max_length=200)]
    base_branch: Annotated[str, StringConstraints(max_length=255)] | None = None
    base_sha: CommitSha | None = None
    branch: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    path: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    local_commit_sha: CommitSha | None = None
    tree_sha: CommitSha | None = None
    publication: PublishedChange | None = None
    checks: list[ExerciseCheck] = Field(min_length=len(_CHECKS), max_length=len(_CHECKS))
    cleaned_up: bool
    """Nothing the exercise created remains: no branch, and any pull request is closed."""
    started_at: AwareDatetime
    completed_at: AwareDatetime
    attested_by: Literal["software_engineer.runtime"] = "software_engineer.runtime"

    @property
    def passed(self) -> bool:
        return self.cleaned_up and all(item.status == "passed" for item in self.checks)


class _Checks:
    def __init__(self) -> None:
        self.results: dict[CheckName, ExerciseCheck] = {}

    def record(self, name: CheckName, passed: bool, detail: str = "") -> bool:
        self.results[name] = ExerciseCheck(
            name=name, status="passed" if passed else "failed", detail=detail[:500]
        )
        return passed

    def all(self) -> list[ExerciseCheck]:
        return [
            self.results.get(name, ExerciseCheck(name=name, status="not_run")) for name in _CHECKS
        ]


def run_publisher_exercise(
    *,
    repo_root: Path,
    publisher: GitHubDraftPullRequestPublisher,
    git: GitRunner,
    work_root: Path,
    owner_id: str,
    exercise_id: UUID,
    clock: Callable[[], datetime],
) -> PublisherExerciseRecord:
    """Run the exercise once; always withdraw anything it published."""

    return _Exercise(
        repo_root=repo_root,
        publisher=publisher,
        git=git,
        work_root=work_root,
        owner_id=owner_id,
        exercise_id=exercise_id,
        clock=clock,
    ).run()


class _Exercise:
    def __init__(
        self,
        *,
        repo_root: Path,
        publisher: GitHubDraftPullRequestPublisher,
        git: GitRunner,
        work_root: Path,
        owner_id: str,
        exercise_id: UUID,
        clock: Callable[[], datetime],
    ) -> None:
        self.repo_root = repo_root
        self.publisher = publisher
        self.git = git
        self.work_root = work_root
        self.owner_id = owner_id
        self.exercise_id = exercise_id
        self.clock = clock
        tag = exercise_id.hex[:12]
        self.branch = f"{EXERCISE_BRANCH_PREFIX}/{tag}"
        self.path = f"{EXERCISE_DIRECTORY}/nexus-publisher-{tag}.md"
        self.scratch = work_root / f"exercise-{tag}"
        self.rederive = work_root / f"rederive-{tag}"
        self.checks = _Checks()
        self.base_branch: str | None = None
        self.base_sha: str | None = None
        self.commit_sha: str | None = None
        self.tree_sha: str | None = None
        self.published: PublishedChange | None = None
        self.cleaned_up = True

    def run(self) -> PublisherExerciseRecord:
        started_at = self.clock()
        try:
            self._steps(started_at)
        finally:
            self._withdraw()
            shutil.rmtree(self.scratch, ignore_errors=True)
            shutil.rmtree(self.rederive, ignore_errors=True)
        return PublisherExerciseRecord(
            exercise_id=self.exercise_id,
            repository=self.publisher.repository,
            base_branch=self.base_branch,
            base_sha=self.base_sha,
            branch=self.branch,
            path=self.path,
            local_commit_sha=self.commit_sha,
            tree_sha=self.tree_sha,
            publication=self.published,
            checks=self.checks.all(),
            cleaned_up=self.cleaned_up,
            started_at=started_at,
            completed_at=max(started_at, self.clock()),
        )

    def _steps(self, now: datetime) -> None:
        checks = self.checks
        try:
            self.base_branch, self.base_sha = self.publisher.default_branch_head()
            checks.record(
                "repository_identity", True, f"{self.publisher.repository}@{self.base_branch}"
            )
        except PublishError as exc:
            checks.record("repository_identity", False, exc.code)
            return
        base_sha = self.base_sha
        try:
            self.git.run(
                [
                    "-C",
                    str(self.repo_root),
                    *_GIT_OPTIONS,
                    "cat-file",
                    "-e",
                    f"{base_sha}^{{commit}}",
                ]
            )
            checks.record("base_available_locally", True, base_sha)
        except GitCommandError:
            checks.record(
                "base_available_locally", False, "fetch the default branch into the checkout"
            )
            return
        try:
            self.commit_sha, patch = _exercise_commit(
                self.git,
                self.repo_root,
                self.scratch,
                base_sha,
                self.branch,
                self.path,
                self.exercise_id,
                now,
            )
            bundle = bundle_from_branch(
                self.git,
                self.scratch,
                base_sha=base_sha,
                commit_sha=self.commit_sha,
                patch=patch,
                work_root=self.rederive,
            )
            self.tree_sha = bundle.tree_sha
            checks.record("bundle_rederived", True, f"tree {bundle.tree_sha}")
        except PublishError as exc:
            checks.record("bundle_rederived", False, exc.code)
            return
        except (GitCommandError, OSError) as exc:
            checks.record("bundle_rederived", False, type(exc).__name__)
            return
        submission = PublishSubmission(
            bundle=bundle,
            branch=self.branch,
            title="NEXUS publisher integration exercise (draft; closed by the exercise)",
            body=(
                "An owner-run integration exercise of the NEXUS resident engineer's "
                "publisher. It adds one harmless file, verifies what GitHub created, and "
                "then closes this draft pull request and deletes its branch. Nothing here "
                f"is meant to merge.\n\nExercise {self.exercise_id}."
            ),
            cycle_id=self.exercise_id,
            diff_sha256=sha256(patch).hexdigest(),
            published_by=ActorIdentity(actor_type=ActorType.HUMAN, actor_id=self.owner_id),
            authority="integration_exercise",
        )
        try:
            self.published = self.publisher.publish(
                replace(submission, bundle=replace(bundle, base_sha=_other_sha(base_sha)))
            )
            checks.record("moved_base_refused", False, "a moved base was published")
            return
        except PublishError as exc:
            if not checks.record("moved_base_refused", exc.code == "base_moved", exc.code):
                return
        try:
            self.published = self.publisher.publish(submission)
        except PublishError as exc:
            checks.record("draft_published", False, exc.code)
            return
        published = self.published
        checks.record(
            "draft_published", True, f"#{published.pull_request_number} {published.branch}"
        )
        try:
            state = self.publisher.pull_request_state(published.pull_request_number)
            checks.record(
                "draft_not_merged",
                state.state == "open"
                and state.draft
                and not state.merged
                and not state.auto_merge
                and state.head_sha == published.remote_commit_sha,
                f"state={state.state} draft={state.draft} merged={state.merged} "
                f"auto_merge={state.auto_merge}",
            )
        except PublishError as exc:
            checks.record("draft_not_merged", False, exc.code)
        try:
            self.publisher.publish(submission)
            checks.record("duplicate_refused", False, "a second publication was accepted")
        except PublishError as exc:
            checks.record("duplicate_refused", exc.code == "branch_exists", exc.code)

    def _withdraw(self) -> None:
        """Close and delete whatever was published, then prove nothing remains."""

        if self.published is None:
            if self.checks.results.get("draft_published") is not None:
                # A failed publication cleans up after itself; confirm no branch is left.
                self.cleaned_up = not _branch_left(self.publisher, self.branch)
            return
        published = self.published
        try:
            self.publisher.withdraw(published, reason="integration exercise complete")
            state = self.publisher.pull_request_state(published.pull_request_number)
            gone = not self.publisher.branch_exists(published.branch)
            self.cleaned_up = state.state == "closed" and not state.merged and gone
            self.checks.record(
                "withdrawn",
                self.cleaned_up,
                f"state={state.state} merged={state.merged} branch_deleted={gone}",
            )
        except PublishError as exc:
            self.cleaned_up = False
            self.checks.record("withdrawn", False, exc.code)


def _exercise_commit(
    git: GitRunner,
    repo_root: Path,
    scratch: Path,
    base_sha: str,
    branch: str,
    path: str,
    exercise_id: UUID,
    now: datetime,
) -> tuple[str, bytes]:
    git.run(
        ["clone", "--quiet", "--no-hardlinks", "--no-checkout", "--", str(repo_root), str(scratch)]
    )
    location = ["-C", str(scratch), *_GIT_OPTIONS]
    git.run([*location, "checkout", "--quiet", "-b", branch, base_sha])
    target = scratch.joinpath(*path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# NEXUS publisher integration exercise\n\n"
        f"Exercise {exercise_id}. This file exists only on a draft pull request that the "
        "exercise closes and whose branch it deletes. It is safe to delete.\n",
        encoding="utf-8",
    )
    git.run([*location, "add", "--", path])
    timestamp = f"@{int(now.timestamp())} +0000"
    identity = {
        "GIT_AUTHOR_NAME": EXERCISE_AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": EXERCISE_AUTHOR_EMAIL,
        "GIT_AUTHOR_DATE": timestamp,
        "GIT_COMMITTER_NAME": EXERCISE_AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": EXERCISE_AUTHOR_EMAIL,
        "GIT_COMMITTER_DATE": timestamp,
    }
    git.run(
        [
            *location,
            "commit",
            "--quiet",
            "--no-verify",
            "-m",
            f"NEXUS publisher integration exercise {exercise_id}",
        ],
        environment_overrides=identity,
    )
    commit = git.run([*location, "rev-parse", "HEAD"]).stdout_text().strip()
    patch = git.run(
        [*location, "diff", "--binary", "--no-ext-diff", "--full-index", base_sha, commit, "--"]
    ).stdout
    return commit, patch


def _other_sha(sha: str) -> str:
    """A syntactically valid SHA that is certainly not ``sha``: a base that moved."""

    return ("0" if sha[0] != "0" else "1") + sha[1:]


def _branch_left(publisher: GitHubDraftPullRequestPublisher, branch: str) -> bool:
    try:
        return publisher.branch_exists(branch)
    except PublishError:
        return True
