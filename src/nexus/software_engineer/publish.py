"""Publishing a validated change as a draft pull request. Never a merge, never ``main``.

The resident engineer cannot push. What it can do, once the owner has said SHIP to an
approval request (or, in ``autonomous_low_risk`` mode, once a LOW-risk change passed every
gate), is open a *draft* pull request through a publisher whose credential lives only in
the process environment. A human reviews and merges.

Before anything reaches GitHub, runtime code rebuilds the tree from the validated patch on
the validated base and refuses to publish a branch whose tree differs. The remote tree is
then checked against that same tree before the branch reference exists. Every failure is a
typed ``PublishError`` whose message carries a code and an API stem, never a response body
and never the token.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID, uuid4, uuid5

import httpx

from nexus.atlas.models import ActorIdentity, ActorType
from nexus.patchforge.workspace import GitCommandError, GitRunner
from nexus.software_engineer.models import (
    SOFTWARE_ENGINEER_AGENT_ID,
    ApprovalRequest,
    ChangeSummary,
    EngineeringCandidate,
    GateResult,
    OwnerDecision,
    PublishedChange,
)
from nexus.software_engineer.report import render_approval_request
from nexus.software_engineer.trust import contains_credential

GITHUB_TOKEN_ENV_DEFAULT = "NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN"
GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
MAX_BUNDLE_FILES = 1000
MAX_FILE_BYTES = 1_000_000
MAX_BUNDLE_BYTES = 5_000_000
MAX_TITLE_CHARS = 200
MAX_BODY_CHARS = 20_000
_PUBLISH_NAMESPACE = UUID("a7b8c9d0-e1f2-4a3b-8c4d-5e6f7a8b9c0d")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_URL = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_SHA1 = re.compile(r"^[a-f0-9]{40}$")
_ALLOWED_MODES = frozenset({"100644", "100755"})
_GIT_OPTIONS = ("-c", "core.autocrlf=false", "-c", "core.hooksPath=/dev/null")


class PublishError(RuntimeError):
    """Publishing was refused or failed. ``code`` is stable; the message never leaks."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        super().__init__(code if detail is None else f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One path in the published tree; ``content`` is ``None`` for a deletion."""

    path: str
    mode: str
    blob_sha: str
    content: bytes | None


@dataclass(frozen=True, slots=True)
class ChangeBundle:
    """Everything a publisher needs, derived by runtime code from the validated branch."""

    base_sha: str
    tree_sha: str
    local_commit_sha: str
    message: str
    author_name: str
    author_email: str
    authored_at: str
    entries: tuple[FileEntry, ...]

    @property
    def content_bytes(self) -> int:
        return sum(len(item.content) for item in self.entries if item.content is not None)


@dataclass(frozen=True, slots=True)
class PublishSubmission:
    """A request to publish; the publisher records who authorized it and why."""

    bundle: ChangeBundle
    branch: str
    title: str
    body: str
    cycle_id: UUID
    diff_sha256: str
    published_by: ActorIdentity
    authority: Literal["owner_decision", "autonomous_low_risk", "integration_exercise"]
    request_id: UUID | None = None
    decision_id: UUID | None = None
    allow_moved_base: bool = False


@dataclass(frozen=True, slots=True)
class PullRequestState:
    state: str
    draft: bool
    merged: bool
    auto_merge: bool
    head_sha: str


class Publisher(Protocol):
    """Opens and withdraws draft pull requests. It never merges."""

    @property
    def provider(self) -> Literal["github"]: ...

    def publish(self, submission: PublishSubmission) -> PublishedChange: ...

    def withdraw(self, published: PublishedChange, *, reason: str) -> None: ...


# -- bundle derivation -------------------------------------------------------------------


def bundle_from_branch(
    git: GitRunner,
    clone: Path,
    *,
    base_sha: str,
    commit_sha: str,
    patch: bytes,
    work_root: Path,
) -> ChangeBundle:
    """Read the candidate commit and prove it is exactly ``patch`` applied to ``base_sha``.

    The proof re-applies the validated patch on the base in a scratch clone and compares
    ``git write-tree`` with the commit's tree. Symlinks, submodules, type changes, renames
    with history, oversized files, and any mismatch are refused.
    """

    if not _SHA1.match(base_sha) or not _SHA1.match(commit_sha):
        raise PublishError("bundle_invalid_sha")
    location = ["-C", str(clone), *_GIT_OPTIONS]
    try:
        parent = git.run([*location, "rev-parse", f"{commit_sha}^"]).stdout_text().strip()
        if parent != base_sha:
            raise PublishError("bundle_parent_mismatch")
        tree = git.run([*location, "rev-parse", f"{commit_sha}^{{tree}}"]).stdout_text().strip()
        if _rederive_tree(git, clone, base_sha, patch, work_root) != tree:
            raise PublishError("bundle_tree_mismatch")
        meta = (
            git.run([*location, "log", "-1", "--format=%an%x00%ae%x00%aI%x00%B", commit_sha])
            .stdout_text()
            .split("\x00", 3)
        )
        if len(meta) != 4:
            raise PublishError("bundle_metadata_unreadable")
        status_output = git.run(
            [
                *location,
                "diff-tree",
                "-r",
                "--no-commit-id",
                "-z",
                "--name-status",
                base_sha,
                commit_sha,
            ]
        ).stdout_text()
        entries = _entries(git, location, commit_sha, status_output)
    except GitCommandError as exc:
        raise PublishError("bundle_git_failed", type(exc).__name__) from exc
    return ChangeBundle(
        base_sha=base_sha,
        tree_sha=tree,
        local_commit_sha=commit_sha,
        message=meta[3].strip() or "Change produced by the NEXUS resident engineer",
        author_name=meta[0],
        author_email=meta[1],
        authored_at=normalize_iso8601(meta[2]),
        entries=entries,
    )


def normalize_iso8601(value: str) -> str:
    """Render a Git ``%aI`` date the same way on every Git version.

    Git 2.55 prints UTC as ``...Z`` where older versions print ``...+00:00``; the bundle
    must not depend on which Git produced it.
    """

    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PublishError("bundle_metadata_unreadable", "author date") from exc
    if parsed.utcoffset() is None:
        raise PublishError("bundle_metadata_unreadable", "naive author date")
    return parsed.isoformat()


def _rederive_tree(
    git: GitRunner, clone: Path, base_sha: str, patch: bytes, work_root: Path
) -> str:
    work_root.mkdir(parents=True, exist_ok=True)
    scratch = work_root / f"rederive-{uuid4().hex[:12]}"
    patch_file = work_root / f"{scratch.name}.patch"
    try:
        git.run(
            ["clone", "--quiet", "--no-hardlinks", "--no-checkout", "--", str(clone), str(scratch)]
        )
        location = ["-C", str(scratch), *_GIT_OPTIONS]
        git.run([*location, "checkout", "--quiet", "--detach", base_sha])
        patch_file.write_bytes(patch)
        git.run([*location, "apply", "--binary", "--whitespace=nowarn", "--", str(patch_file)])
        git.run([*location, "add", "--all", "--", "."])
        return git.run([*location, "write-tree"]).stdout_text().strip()
    except OSError as exc:
        raise PublishError("bundle_scratch_failed", type(exc).__name__) from exc
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        patch_file.unlink(missing_ok=True)


def _entries(
    git: GitRunner, location: list[str], commit_sha: str, status_output: str
) -> tuple[FileEntry, ...]:
    tokens = [token for token in status_output.split("\x00") if token]
    if len(tokens) % 2:
        raise PublishError("bundle_status_unreadable")
    pairs = list(zip(tokens[::2], tokens[1::2], strict=True))
    if not pairs:
        raise PublishError("bundle_empty")
    if len(pairs) > MAX_BUNDLE_FILES:
        raise PublishError("bundle_too_many_files")
    entries: list[FileEntry] = []
    total = 0
    for status, path in pairs:
        if status == "D":
            entries.append(FileEntry(path=path, mode="100644", blob_sha="0" * 40, content=None))
            continue
        if status not in {"A", "M"}:
            raise PublishError("bundle_unsupported_change", status)
        listing = git.run([*location, "ls-tree", "-l", "-z", commit_sha, "--", path]).stdout_text()
        record = listing.split("\x00", 1)[0]
        info, _, listed_path = record.partition("\t")
        parts = info.split()
        if listed_path != path or len(parts) != 4 or parts[1] != "blob":
            raise PublishError("bundle_unsupported_entry", path)
        mode, _, blob_sha, size_text = parts
        if mode not in _ALLOWED_MODES or not _SHA1.match(blob_sha) or not size_text.isdigit():
            raise PublishError("bundle_unsupported_mode", path)
        size = int(size_text)
        if size > MAX_FILE_BYTES:
            raise PublishError("bundle_file_too_large", path)
        total += size
        if total > MAX_BUNDLE_BYTES:
            raise PublishError("bundle_too_large")
        content = git.run(
            [*location, "cat-file", "blob", blob_sha], output_limit=MAX_FILE_BYTES + 1
        ).stdout
        if len(content) != size:
            raise PublishError("bundle_blob_mismatch", path)
        entries.append(FileEntry(path=path, mode=mode, blob_sha=blob_sha, content=content))
    return tuple(sorted(entries, key=lambda item: item.path))


# -- rendering ---------------------------------------------------------------------------


def render_pull_request(
    *,
    candidate: EngineeringCandidate,
    change: ChangeSummary,
    gates: Sequence[GateResult],
    request: ApprovalRequest | None,
    decision: OwnerDecision | None,
    cycle_id: UUID,
) -> tuple[str, str]:
    """Title and body for the draft pull request, from runtime evidence only."""

    title = candidate.title.strip().splitlines()[0][:MAX_TITLE_CHARS]
    if decision is not None:
        authority = (
            f"Owner decision: {decision.verdict.value.upper()} by {decision.decided_by.actor_id} "
            f"via {decision.channel} at {decision.decided_at.isoformat()} "
            f"(decision {decision.decision_id}). Reason: {decision.reason}"
        )
    else:
        authority = (
            "Opened autonomously in autonomous_low_risk mode: LOW risk, every gate passed, "
            "self-review clean. No human has approved this change yet."
        )
    validation = (
        "\n".join(
            f"- {item.gate.value}: {item.status.value}"
            + (f" (evidence sha256 {item.evidence_sha256})" if item.evidence_sha256 else "")
            for item in gates
        )
        or "- no gates recorded"
    )
    evidence = [
        f"- diff sha256: {change.diff_sha256}",
        f"- base commit: {change.base_sha}",
    ]
    if change.patch_result_sha256:
        evidence.append(f"- PatchForge result sha256: {change.patch_result_sha256}")
    if change.sentinel_verdict_sha256:
        evidence.append(f"- SentinelQA verdict sha256: {change.sentinel_verdict_sha256}")
    sections = [
        "Draft pull request opened by the NEXUS resident Software Engineer. It never merges: "
        "a human reviews, and CI on this pull request is the current-base validation.",
        "",
        f"Cycle: {cycle_id}",
        f"Candidate: {candidate.candidate_id} ({candidate.category.value})",
        authority,
        "",
        "## Validation on the candidate base",
        validation,
        "",
        "## Evidence",
        *evidence,
    ]
    if request is not None:
        sections.extend(["", "## Approval request as sent to the owner", "", "```text"])
        sections.append(render_approval_request(request).replace("```", "'''"))
        sections.append("```")
    body = "\n".join(sections)
    if len(body) > MAX_BODY_CHARS:
        body = body[: MAX_BODY_CHARS - 20].rstrip() + "\n[truncated]\n```"
    if contains_credential(title) or contains_credential(body):
        raise PublishError("secret_blocked")
    return title, body


def parse_github_repository(value: str) -> str:
    """``owner/repo`` from a GitHub URL or a bare ``owner/repo``; anything else is refused."""

    candidate = value.strip()
    match = _GITHUB_URL.match(candidate)
    if match is not None:
        candidate = f"{match.group('owner')}/{match.group('repo')}"
    if not _REPOSITORY.match(candidate) or ".." in candidate:
        raise PublishError("repository_unsupported")
    return candidate


# -- publishers --------------------------------------------------------------------------


class RecordingPublisher:
    """In-memory publisher for tests and evaluation; it publishes nothing anywhere."""

    provider: Literal["github"] = "github"

    def __init__(
        self,
        *,
        repository: str = "example/fixture",
        base_head: str | None = None,
        fail_with: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.base_head = base_head
        self.fail_with = fail_with
        self.clock = clock or (lambda: datetime.now(UTC))
        self.submissions: list[PublishSubmission] = []
        self.withdrawn: list[tuple[PublishedChange, str]] = []

    def publish(self, submission: PublishSubmission) -> PublishedChange:
        if self.fail_with is not None:
            raise PublishError(self.fail_with)
        head = self.base_head or submission.bundle.base_sha
        if head != submission.bundle.base_sha and not submission.allow_moved_base:
            raise PublishError("base_moved")
        self.submissions.append(submission)
        number = len(self.submissions)
        return PublishedChange(
            publication_id=uuid5(_PUBLISH_NAMESPACE, f"{submission.cycle_id}:{number}"),
            cycle_id=submission.cycle_id,
            request_id=submission.request_id,
            decision_id=submission.decision_id,
            authority=submission.authority,
            provider="github",
            repository=self.repository,
            base_branch="main",
            base_sha=submission.bundle.base_sha,
            base_head_at_publish=head,
            branch=submission.branch,
            tree_sha=submission.bundle.tree_sha,
            local_commit_sha=submission.bundle.local_commit_sha,
            remote_commit_sha=submission.bundle.local_commit_sha,
            diff_sha256=submission.diff_sha256,
            pull_request_number=number,
            pull_request_url=f"https://example.invalid/{self.repository}/pull/{number}",
            published_by=submission.published_by,
            published_at=self.clock(),
        )

    def withdraw(self, published: PublishedChange, *, reason: str) -> None:
        self.withdrawn.append((published, reason))


@dataclass(slots=True)
class _GitHubSession:
    client: httpx.Client
    repository: str
    created_ref: str | None = None
    calls: list[str] = field(default_factory=list)


class GitHubDraftPullRequestPublisher:
    """Create a branch and a draft pull request through the GitHub REST API.

    The token is read from ``token_env`` at publish time, sent only as a request header,
    and never stored, logged, or included in an error. The publisher refuses when the base
    branch moved (unless the submission allows it), when the branch already exists, when a
    blob or the tree hashes differently from the local commit, and when the repository does
    not accept draft pull requests. It never merges and never touches the base branch.
    """

    provider: Literal["github"] = "github"

    def __init__(
        self,
        repository: str,
        *,
        token_env: str = GITHUB_TOKEN_ENV_DEFAULT,
        api_base: str = GITHUB_API_BASE,
        timeout_seconds: float = 30.0,
        client_factory: Callable[[Mapping[str, str]], httpx.Client] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not _REPOSITORY.match(repository):
            raise PublishError("repository_unsupported")
        if not api_base.startswith("https://"):
            raise PublishError("api_base_insecure")
        self.repository = repository
        self.token_env = token_env
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client_factory = client_factory or (
            lambda headers: httpx.Client(
                base_url=self.api_base, headers=dict(headers), timeout=self.timeout_seconds
            )
        )
        self.clock = clock or (lambda: datetime.now(UTC))

    # -- Publisher -----------------------------------------------------------------------

    def publish(self, submission: PublishSubmission) -> PublishedChange:
        bundle = submission.bundle
        if not bundle.entries:
            raise PublishError("bundle_empty")
        if bundle.content_bytes > MAX_BUNDLE_BYTES:
            raise PublishError("bundle_too_large")
        if contains_credential(submission.title) or contains_credential(submission.body):
            raise PublishError("secret_blocked")
        with self._session() as session:
            repo = self._get(session, f"/repos/{self.repository}")
            base_branch = _string(repo, "default_branch", "repos")
            base_ref = self._get(session, f"/repos/{self.repository}/git/ref/heads/{base_branch}")
            base_head = _string(_mapping(base_ref, "object", "git/ref"), "sha", "git/ref")
            if base_head != bundle.base_sha and not submission.allow_moved_base:
                raise PublishError("base_moved")
            status, base_commit = self._request(
                session,
                "GET",
                f"/repos/{self.repository}/git/commits/{bundle.base_sha}",
                ok=(200, 404),
            )
            if status == 404:
                raise PublishError("base_not_in_remote")
            base_tree = _string(_mapping(base_commit, "tree", "git/commits"), "sha", "git/commits")
            branch_status, _ = self._request(
                session,
                "GET",
                f"/repos/{self.repository}/git/ref/heads/{submission.branch}",
                ok=(200, 404),
            )
            if branch_status == 200:
                raise PublishError("branch_exists")
            tree_entries: list[dict[str, object]] = []
            for entry in bundle.entries:
                if entry.content is None:
                    tree_entries.append(
                        {"path": entry.path, "mode": entry.mode, "type": "blob", "sha": None}
                    )
                    continue
                blob = self._post(
                    session,
                    f"/repos/{self.repository}/git/blobs",
                    {
                        "content": base64.b64encode(entry.content).decode("ascii"),
                        "encoding": "base64",
                    },
                )
                if _string(blob, "sha", "git/blobs") != entry.blob_sha:
                    raise PublishError("blob_mismatch", entry.path)
                tree_entries.append(
                    {"path": entry.path, "mode": entry.mode, "type": "blob", "sha": entry.blob_sha}
                )
            tree = self._post(
                session,
                f"/repos/{self.repository}/git/trees",
                {"base_tree": base_tree, "tree": tree_entries},
            )
            if _string(tree, "sha", "git/trees") != bundle.tree_sha:
                raise PublishError("tree_mismatch")
            identity = {
                "name": bundle.author_name,
                "email": bundle.author_email,
                "date": bundle.authored_at,
            }
            commit = self._post(
                session,
                f"/repos/{self.repository}/git/commits",
                {
                    "message": bundle.message,
                    "tree": bundle.tree_sha,
                    "parents": [bundle.base_sha],
                    "author": identity,
                    "committer": identity,
                },
            )
            remote_commit = _string(commit, "sha", "git/commits")
            # The remote SHA may differ from the local one (GitHub can sign API commits), so
            # the commit is verified by what it contains: exactly the verified tree, on
            # exactly the validated base, with the message and identities that were sent.
            if not _commit_matches(commit, bundle):
                raise PublishError("commit_mismatch")
            ref_status, ref = self._request(
                session,
                "POST",
                f"/repos/{self.repository}/git/refs",
                json={"ref": f"refs/heads/{submission.branch}", "sha": remote_commit},
                ok=(201, 422),
            )
            if ref_status == 422:
                raise PublishError("branch_exists")
            session.created_ref = submission.branch
            if _string(_mapping(ref, "object", "git/refs"), "sha", "git/refs") != remote_commit:
                raise PublishError("ref_mismatch")
            pull_status, pull = self._request(
                session,
                "POST",
                f"/repos/{self.repository}/pulls",
                json={
                    "title": submission.title,
                    "head": submission.branch,
                    "base": base_branch,
                    "body": submission.body,
                    "draft": True,
                    "maintainer_can_modify": True,
                },
                ok=(201, 422),
            )
            if pull_status == 422:
                self._delete_ref(session)
                raise PublishError("draft_unsupported_or_rejected")
            number = _integer(pull, "number", "pulls")
            url = _string(pull, "html_url", "pulls")
            head_sha = _string(_mapping(pull, "head", "pulls"), "sha", "pulls")
            pull_base = _string(_mapping(pull, "base", "pulls"), "ref", "pulls")
            if head_sha != remote_commit or pull_base != base_branch:
                self._request(
                    session,
                    "PATCH",
                    f"/repos/{self.repository}/pulls/{number}",
                    json={"state": "closed"},
                    ok=(200,),
                )
                self._delete_ref(session)
                raise PublishError("pull_request_mismatch")
            if pull.get("draft") is not True:
                self._request(
                    session,
                    "PATCH",
                    f"/repos/{self.repository}/pulls/{number}",
                    json={"state": "closed"},
                    ok=(200,),
                )
                self._delete_ref(session)
                raise PublishError("draft_unsupported")
            session.created_ref = None
            return PublishedChange(
                publication_id=uuid5(
                    _PUBLISH_NAMESPACE, f"{self.repository}:{submission.branch}:{remote_commit}"
                ),
                cycle_id=submission.cycle_id,
                request_id=submission.request_id,
                decision_id=submission.decision_id,
                authority=submission.authority,
                provider="github",
                repository=self.repository,
                base_branch=base_branch,
                base_sha=bundle.base_sha,
                base_head_at_publish=base_head,
                branch=submission.branch,
                tree_sha=bundle.tree_sha,
                local_commit_sha=bundle.local_commit_sha,
                remote_commit_sha=remote_commit,
                diff_sha256=submission.diff_sha256,
                pull_request_number=number,
                pull_request_url=url,
                published_by=submission.published_by,
                published_at=self.clock(),
            )

    # -- read-only inspection (used by the owner-run integration exercise) -----------------

    def default_branch_head(self) -> tuple[str, str]:
        """``(default branch, head SHA)`` of the repository, read-only."""

        with self._session() as session:
            repo = self._get(session, f"/repos/{self.repository}")
            branch = _string(repo, "default_branch", "repos")
            ref = self._get(session, f"/repos/{self.repository}/git/ref/heads/{branch}")
            return branch, _string(_mapping(ref, "object", "git/ref"), "sha", "git/ref")

    def pull_request_state(self, number: int) -> PullRequestState:
        with self._session() as session:
            pull = self._get(session, f"/repos/{self.repository}/pulls/{number}")
            merged = pull.get("merged")
            draft = pull.get("draft")
            return PullRequestState(
                state=_string(pull, "state", "pulls"),
                draft=draft is True,
                merged=merged is True,
                auto_merge=pull.get("auto_merge") is not None,
                head_sha=_string(_mapping(pull, "head", "pulls"), "sha", "pulls"),
            )

    def branch_exists(self, branch: str) -> bool:
        with self._session() as session:
            status, _ = self._request(
                session,
                "GET",
                f"/repos/{self.repository}/git/ref/heads/{branch}",
                ok=(200, 404),
            )
            return status == 200

    def withdraw(self, published: PublishedChange, *, reason: str) -> None:
        """Close the draft pull request and delete its branch. Nothing else is touched."""

        if published.repository != self.repository:
            raise PublishError("repository_mismatch")
        with self._session() as session:
            self._request(
                session,
                "PATCH",
                f"/repos/{self.repository}/pulls/{published.pull_request_number}",
                json={"state": "closed"},
                ok=(200,),
            )
            session.created_ref = published.branch
            self._delete_ref(session, strict=True)

    # -- HTTP ------------------------------------------------------------------------------

    def _session(self) -> _SessionContext:
        token = os.environ.get(self.token_env, "").strip()
        if not token:
            raise PublishError("credentials_missing")
        if any(character.isspace() or not character.isprintable() for character in token):
            raise PublishError("credentials_invalid")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "nexus-resident-software-engineer",
        }
        client = self._client_factory(headers)
        del token, headers
        return _SessionContext(self, _GitHubSession(client=client, repository=self.repository))

    def _get(self, session: _GitHubSession, path: str) -> Mapping[str, object]:
        return self._request(session, "GET", path, ok=(200,))[1]

    def _post(
        self, session: _GitHubSession, path: str, json: Mapping[str, object]
    ) -> Mapping[str, object]:
        return self._request(session, "POST", path, json=json, ok=(201,))[1]

    def _request(
        self,
        session: _GitHubSession,
        method: str,
        path: str,
        *,
        ok: tuple[int, ...],
        json: Mapping[str, object] | None = None,
    ) -> tuple[int, Mapping[str, object]]:
        stem = _stem(path)
        session.calls.append(f"{method} {stem}")
        try:
            response = session.client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise PublishError("transport_error", f"{stem} {type(exc).__name__}") from exc
        code = response.status_code
        if code in ok:
            # 204 No Content (a deleted reference) and an expected 404 carry no JSON body.
            if code in (204, 404):
                return code, {}
            try:
                payload = response.json()
            except ValueError as exc:
                raise PublishError("response_unreadable", stem) from exc
            if not isinstance(payload, dict):
                raise PublishError("response_unreadable", stem)
            return code, payload
        if code == 401:
            raise PublishError("credentials_rejected", stem)
        if code == 429 or (code == 403 and response.headers.get("x-ratelimit-remaining") == "0"):
            raise PublishError("rate_limited", stem)
        if code == 403:
            raise PublishError("forbidden", stem)
        if code == 404:
            raise PublishError("not_found", stem)
        raise PublishError(f"http_{code}", stem)

    def _delete_ref(self, session: _GitHubSession, *, strict: bool = False) -> None:
        if session.created_ref is None:
            return
        branch, session.created_ref = session.created_ref, None
        try:
            self._request(
                session, "DELETE", f"/repos/{self.repository}/git/refs/heads/{branch}", ok=(204,)
            )
        except PublishError:
            if strict:
                raise


class _SessionContext:
    def __init__(self, publisher: GitHubDraftPullRequestPublisher, session: _GitHubSession) -> None:
        self._publisher = publisher
        self._session = session

    def __enter__(self) -> _GitHubSession:
        return self._session

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if exc is not None:
                self._publisher._delete_ref(self._session)
        finally:
            self._session.client.close()


def _commit_matches(commit: Mapping[str, object], bundle: ChangeBundle) -> bool:
    """The created commit carries the verified tree, the validated base as its only parent,
    and the message, author, committer, and date that were sent."""

    tree = commit.get("tree")
    parents = commit.get("parents")
    message = commit.get("message")
    if not isinstance(tree, Mapping) or tree.get("sha") != bundle.tree_sha:
        return False
    if not isinstance(parents, list) or [
        item.get("sha") if isinstance(item, Mapping) else None for item in parents
    ] != [bundle.base_sha]:
        return False
    if not isinstance(message, str) or message.rstrip("\n") != bundle.message.rstrip("\n"):
        return False
    expected = datetime.fromisoformat(bundle.authored_at)
    for role in ("author", "committer"):
        person = commit.get(role)
        if not isinstance(person, Mapping):
            return False
        date = person.get("date")
        if (
            person.get("name") != bundle.author_name
            or person.get("email") != bundle.author_email
            or not isinstance(date, str)
        ):
            return False
        try:
            if datetime.fromisoformat(normalize_iso8601(date)) != expected:
                return False
        except PublishError:
            return False
    return True


def agent_publisher_identity() -> ActorIdentity:
    return ActorIdentity(actor_type=ActorType.AGENT, actor_id=SOFTWARE_ENGINEER_AGENT_ID)


def _stem(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    # /repos/{owner}/{repo}/git/refs/... -> git/refs ; /repos/{owner}/{repo} -> repos
    if len(parts) >= 4 and parts[0] == "repos":
        return "/".join(parts[3:5])
    return parts[0] if parts else "/"


def _mapping(payload: Mapping[str, object], key: str, stem: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise PublishError("response_unreadable", f"{stem}.{key}")
    return value


def _string(payload: Mapping[str, object], key: str, stem: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PublishError("response_unreadable", f"{stem}.{key}")
    return value


def _integer(payload: Mapping[str, object], key: str, stem: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PublishError("response_unreadable", f"{stem}.{key}")
    return value


__all__ = [
    "GITHUB_API_BASE",
    "GITHUB_TOKEN_ENV_DEFAULT",
    "MAX_BODY_CHARS",
    "MAX_BUNDLE_BYTES",
    "MAX_BUNDLE_FILES",
    "MAX_FILE_BYTES",
    "MAX_TITLE_CHARS",
    "ChangeBundle",
    "FileEntry",
    "GitHubDraftPullRequestPublisher",
    "PublishError",
    "PublishSubmission",
    "Publisher",
    "PullRequestState",
    "RecordingPublisher",
    "agent_publisher_identity",
    "bundle_from_branch",
    "normalize_iso8601",
    "parse_github_repository",
    "render_pull_request",
]
