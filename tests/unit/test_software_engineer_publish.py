"""Publishing: owner decision, tree re-derivation, GitHub draft pull requests, fail-closed."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha1, sha256
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from nexus.atlas.models import ActorType
from nexus.patchforge.canonical import canonical_json
from nexus.patchforge.e2e import FixtureRepository, materialize_fixture
from nexus.patchforge.sandbox import SandboxExecution, SandboxRequest, SandboxStatus
from nexus.patchforge.workspace import GitRunner
from nexus.software_engineer import __main__ as cli
from nexus.software_engineer.approval import (
    ApprovalError,
    decide,
    load_cycle_record,
    load_publication,
    publish_approved_change,
)
from nexus.software_engineer.config import SoftwareEngineerSettings
from nexus.software_engineer.cycle import EngineeringCycle, ExecutorError
from nexus.software_engineer.executor import (
    BRANCH_PREFIX,
    COMMIT_AUTHOR_EMAIL,
    COMMIT_AUTHOR_NAME,
    PatchForgeExecutor,
)
from nexus.software_engineer.inspect import CandidateGenerator, RepositoryInspector
from nexus.software_engineer.memory import EngineerMemory, EngineerMemoryStore, EpistemicStatus
from nexus.software_engineer.models import (
    CandidateEstimate,
    ChangeCategory,
    ChangeSummary,
    CycleDecision,
    CycleMode,
    EngineeringCandidate,
    EngineeringSignal,
    GateStatus,
    OwnerVerdict,
    PublishedChange,
    ValidationGate,
)
from nexus.software_engineer.notify import Notifier, RecordingTransport
from nexus.software_engineer.publish import (
    GitHubDraftPullRequestPublisher,
    PublishError,
    PublishSubmission,
    RecordingPublisher,
    agent_publisher_identity,
    bundle_from_branch,
    normalize_iso8601,
    parse_github_repository,
    render_pull_request,
)
from nexus.software_engineer.recipes import Recipe, RecipeCommands, recipe_commands
from nexus.software_engineer.sandbox import LocalProcessSandbox

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
LATER = datetime(2026, 9, 29, 18, tzinfo=UTC)
CYCLE = UUID(int=970)
CANDIDATE = UUID(int=971)
REPOSITORY_URL = "https://github.com/example/fixture"
TOKEN = "github_pat_TESTONLY_never_used_0123456789"
UNFORMATTED = "def add(a,b):\n    return a+b\n"
FORMATTED = "def add(a, b):\n    return a + b\n"
TEST = "from pkg.module import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
FIXTURE = FixtureRepository(
    name="publish-fixture",
    files={
        "src/pkg/__init__.py": "",
        "src/pkg/module.py": UNFORMATTED,
        "tests/test_module.py": TEST,
        "pyproject.toml": "[tool.ruff]\nline-length = 88\n",
        "README.md": "# fixture\n",
    },
)


# -- fixtures ----------------------------------------------------------------------------


def _commands(recipe: Recipe) -> RecipeCommands:
    return recipe_commands(recipe, typecheck=("-m", "mypy", "-p", "pkg"))


def _candidate() -> EngineeringCandidate:
    return EngineeringCandidate(
        candidate_id=CANDIDATE,
        title="Repair source formatting drift",
        rationale="The repository's formatter check fails; its tool can repair this mechanically.",
        category=ChangeCategory.FORMATTING,
        expected_paths=["src/pkg/module.py"],
        signal_ids=[UUID(int=1)],
        estimate=CandidateEstimate(value=50, urgency=60, confidence=85, cost=20),
    )


class _OneCandidate(CandidateGenerator):
    def generate(
        self, signals: Sequence[EngineeringSignal], memories: Sequence[EngineerMemory] = ()
    ) -> list[EngineeringCandidate]:
        return [_candidate().model_copy(update={"signal_ids": [signals[0].signal_id]})]


class _Workbench:
    """A fixture repository, a real executor, and a propose-mode cycle's persisted state."""

    def __init__(self, tmp_path: Path, *, publisher: RecordingPublisher | None = None) -> None:
        self.git = GitRunner(tmp_path / "git")
        self.repo = tmp_path / "repo"
        materialize_fixture(FIXTURE, self.repo, self.git, NOW)
        self.state_root = tmp_path / "state"
        self.settings = SoftwareEngineerSettings(  # type: ignore[call-arg]
            _env_file=None,
            enabled=True,
            mode=CycleMode.PROPOSE,
            repository_url=REPOSITORY_URL,
            state_root=self.state_root,
            memory_path=self.state_root / "memory.sqlite3",
            max_runtime_seconds=3600,
        )
        self.executor = PatchForgeExecutor(
            repo_root=self.repo,
            repository_url=REPOSITORY_URL,
            sandbox=LocalProcessSandbox(allow_local_process=True, clock=lambda: NOW),
            run_root=self.state_root / "runs",
            clock=lambda: NOW,
            git=self.git,
            commands=_commands,
            publisher=publisher,
        )
        self.transport = RecordingTransport()
        self.memory = EngineerMemoryStore(self.settings.memory_path)

    def run_cycle(self) -> None:
        cycle = EngineeringCycle(
            settings=self.settings,
            inspector=RepositoryInspector(self.repo, git=self.git, clock=lambda: NOW),
            memory=self.memory,
            notifier=Notifier(self.transport, clock=lambda: NOW),
            executor=self.executor,
            generator=_OneCandidate(),
            clock=lambda: NOW,
            cycle_id=CYCLE,
        )
        record, _ = cycle.run()
        assert record.decision is CycleDecision.REQUEST_APPROVAL, record.decision_reasons
        assert record.change is not None and record.change.commit_sha is not None

    @property
    def run_root(self) -> Path:
        return self.state_root / "runs" / str(CYCLE) / CANDIDATE.hex[:12]

    def git_text(self, *arguments: str, cwd: Path | None = None) -> str:
        location = ["-C", str(cwd or self.run_root / "branch")]
        return self.git.run([*location, *arguments]).stdout_text().strip()

    def decide(self, verdict: OwnerVerdict, reason: str = "looks right") -> None:
        decide(
            state_root=self.state_root,
            memory=self.memory,
            owner_id=self.settings.owner_id,
            cycle="latest",
            verdict=verdict,
            reason=reason,
            now=LATER,
        )

    def publish(
        self, publisher: GitHubDraftPullRequestPublisher | RecordingPublisher, **kwargs: bool
    ) -> PublishedChange:
        return publish_approved_change(
            state_root=self.state_root,
            memory=self.memory,
            owner_id=self.settings.owner_id,
            cycle="latest",
            publisher=publisher,
            git=self.git,
            now=LATER,
            **kwargs,
        )


class FakeGitHub:
    """Just enough of the GitHub REST API to prove what the publisher sends and checks."""

    def __init__(
        self,
        *,
        base_sha: str,
        base_tree: str,
        tree_sha: str,
        base_head: str | None = None,
        existing_branches: Sequence[str] = (),
        draft_supported: bool = True,
        unauthorized: bool = False,
    ) -> None:
        self.base_sha = base_sha
        self.base_tree = base_tree
        self.tree_sha = tree_sha
        self.base_head = base_head or base_sha
        self.refs: dict[str, str] = {f"heads/{name}": "f" * 40 for name in existing_branches}
        self.draft_supported = draft_supported
        self.unauthorized = unauthorized
        self.blobs: dict[str, bytes] = {}
        self.trees: list[Mapping[str, object]] = []
        self.commits: list[Mapping[str, object]] = []
        self.pulls: list[Mapping[str, object]] = []
        self.patches: list[tuple[int, Mapping[str, object]]] = []
        self.requests: list[httpx.Request] = []

    def client(self, headers: Mapping[str, str]) -> httpx.Client:
        return httpx.Client(
            base_url="https://api.github.invalid",
            headers=dict(headers),
            transport=httpx.MockTransport(self),
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.unauthorized:
            return httpx.Response(401, json={"message": "Bad credentials"})
        path = request.url.path.removeprefix("/repos/example/fixture")
        body = json.loads(request.content) if request.content else {}
        method = request.method
        if method == "GET" and path == "":
            return httpx.Response(200, json={"default_branch": "main"})
        if method == "GET" and path == "/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": self.base_head}})
        if method == "GET" and path.startswith("/git/ref/"):
            ref = path.removeprefix("/git/ref/")
            if ref in self.refs:
                return httpx.Response(200, json={"object": {"sha": self.refs[ref]}})
            return httpx.Response(404, json={"message": "Not Found"})
        if method == "GET" and path.startswith("/git/commits/"):
            if path.removeprefix("/git/commits/") == self.base_sha:
                return httpx.Response(200, json={"tree": {"sha": self.base_tree}})
            return httpx.Response(404, json={"message": "Not Found"})
        if method == "POST" and path == "/git/blobs":
            assert body["encoding"] == "base64"
            content = base64.b64decode(body["content"])
            sha = sha1(b"blob %d\0" % len(content) + content).hexdigest()
            self.blobs[sha] = content
            return httpx.Response(201, json={"sha": sha})
        if method == "POST" and path == "/git/trees":
            self.trees.append(body)
            return httpx.Response(201, json={"sha": self.tree_sha})
        if method == "POST" and path == "/git/commits":
            self.commits.append(body)
            sha = sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()
            return httpx.Response(201, json={"sha": sha})
        if method == "POST" and path == "/git/refs":
            ref = str(body["ref"]).removeprefix("refs/")
            if ref in self.refs:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self.refs[ref] = str(body["sha"])
            return httpx.Response(201, json={"ref": body["ref"]})
        if method == "DELETE" and path.startswith("/git/refs/"):
            self.refs.pop(path.removeprefix("/git/refs/"), None)
            return httpx.Response(204)
        if method == "POST" and path == "/pulls":
            if not self.draft_supported:
                return httpx.Response(
                    422, json={"message": "Draft pull requests are not supported"}
                )
            self.pulls.append(body)
            number = len(self.pulls)
            return httpx.Response(
                201,
                json={
                    "number": number,
                    "html_url": f"https://github.invalid/example/fixture/pull/{number}",
                    "draft": True,
                },
            )
        if method == "PATCH" and path.startswith("/pulls/"):
            self.patches.append((int(path.removeprefix("/pulls/")), body))
            return httpx.Response(200, json={"state": "closed"})
        return httpx.Response(500, json={"message": f"unexpected {method} {path}"})


def _publisher(fake: FakeGitHub, **kwargs: object) -> GitHubDraftPullRequestPublisher:
    return GitHubDraftPullRequestPublisher(
        "example/fixture",
        api_base="https://api.github.invalid",
        client_factory=fake.client,
        clock=lambda: LATER,
        **kwargs,  # type: ignore[arg-type]
    )


def _fake_for(bench: _Workbench, **kwargs: object) -> FakeGitHub:
    record = load_cycle_record(bench.state_root)
    assert record.change is not None and record.change.commit_sha is not None
    return FakeGitHub(
        base_sha=record.change.base_sha,
        base_tree=bench.git_text("rev-parse", f"{record.change.base_sha}^{{tree}}"),
        tree_sha=bench.git_text("rev-parse", f"{record.change.commit_sha}^{{tree}}"),
        **kwargs,  # type: ignore[arg-type]
    )


# -- bundle derivation -------------------------------------------------------------------


def test_bundle_reproduces_the_validated_tree_from_the_patch(tmp_path: Path) -> None:
    bench = _Workbench(tmp_path)
    bench.run_cycle()
    change = load_cycle_record(bench.state_root).change
    assert change is not None and change.commit_sha is not None
    patch = (bench.run_root / "candidate.patch").read_bytes()
    bundle = bundle_from_branch(
        bench.git,
        bench.run_root / "branch",
        base_sha=change.base_sha,
        commit_sha=change.commit_sha,
        patch=patch,
        work_root=tmp_path / "work",
    )
    assert [item.path for item in bundle.entries] == ["src/pkg/module.py"]
    entry = bundle.entries[0]
    assert entry.content == FORMATTED.encode() and entry.mode == "100644"
    assert entry.blob_sha == bench.git_text("rev-parse", f"{change.commit_sha}:src/pkg/module.py")
    assert bundle.tree_sha == bench.git_text("rev-parse", f"{change.commit_sha}^{{tree}}")
    assert bundle.author_name == COMMIT_AUTHOR_NAME and bundle.author_email == COMMIT_AUTHOR_EMAIL
    assert bundle.authored_at.startswith("2026-09-29T12:00:00")
    assert "resident engineer" in bundle.message
    assert not list((tmp_path / "work").iterdir()), "scratch clones are removed"


def test_bundle_refuses_anything_but_the_validated_patch_on_the_validated_base(
    tmp_path: Path,
) -> None:
    bench = _Workbench(tmp_path)
    bench.run_cycle()
    change = load_cycle_record(bench.state_root).change
    assert change is not None and change.commit_sha is not None
    patch = (bench.run_root / "candidate.patch").read_bytes()
    other_patch = patch.replace(b"return a + b", b"return b + a")
    with pytest.raises(PublishError, match="bundle_tree_mismatch"):
        bundle_from_branch(
            bench.git,
            bench.run_root / "branch",
            base_sha=change.base_sha,
            commit_sha=change.commit_sha,
            patch=other_patch,
            work_root=tmp_path / "work",
        )
    with pytest.raises(PublishError, match="bundle_parent_mismatch"):
        bundle_from_branch(
            bench.git,
            bench.run_root / "branch",
            base_sha=change.commit_sha,
            commit_sha=change.commit_sha,
            patch=patch,
            work_root=tmp_path / "work",
        )
    with pytest.raises(PublishError, match="bundle_invalid_sha"):
        bundle_from_branch(
            bench.git,
            bench.run_root / "branch",
            base_sha="HEAD",
            commit_sha=change.commit_sha,
            patch=patch,
            work_root=tmp_path / "work",
        )


# -- owner decision and publication ----------------------------------------------------


def test_owner_ship_decision_publishes_a_verified_draft_pull_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Workbench(tmp_path)
    bench.run_cycle()
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN", TOKEN)
    fake = _fake_for(bench)
    with pytest.raises(ApprovalError, match="not_decided"):
        bench.publish(_publisher(fake))
    assert fake.requests == []
    bench.decide(OwnerVerdict.SHIP, "Formatting drift is real and the tool fixed only that.")
    with pytest.raises(ApprovalError, match="already_decided"):
        bench.decide(OwnerVerdict.REJECT)
    published = bench.publish(_publisher(fake))

    record = load_cycle_record(bench.state_root)
    assert record.change is not None and record.approval_request is not None
    assert published.authority == "owner_decision"
    assert published.request_id == record.approval_request.request_id
    assert published.published_by.actor_type is ActorType.HUMAN
    assert published.published_by.actor_id == bench.settings.owner_id
    assert published.branch == f"{BRANCH_PREFIX}/{CYCLE.hex[:12]}"
    assert published.base_sha == record.change.base_sha == published.base_head_at_publish
    assert (
        published.tree_sha == fake.tree_sha and published.diff_sha256 == record.change.diff_sha256
    )
    assert published.pull_request_number == 1 and published.draft is True
    # The remote objects are exactly the local ones.
    assert list(fake.blobs.values()) == [FORMATTED.encode()]
    assert fake.trees == [
        {
            "base_tree": fake.base_tree,
            "tree": [
                {
                    "path": "src/pkg/module.py",
                    "mode": "100644",
                    "type": "blob",
                    "sha": next(iter(fake.blobs)),
                }
            ],
        }
    ]
    commit = fake.commits[0]
    assert commit["parents"] == [record.change.base_sha] and commit["tree"] == fake.tree_sha
    assert commit["author"] == {
        "name": COMMIT_AUTHOR_NAME,
        "email": COMMIT_AUTHOR_EMAIL,
        "date": "2026-09-29T12:00:00+00:00",
    }
    assert fake.refs[f"heads/{published.branch}"] == published.remote_commit_sha
    pull = fake.pulls[0]
    assert pull["draft"] is True and pull["base"] == "main" and pull["head"] == published.branch
    assert pull["title"] == "Repair source formatting drift"
    body = str(pull["body"])
    assert "READY FOR REVIEW" in body and "Owner decision: SHIP" in body
    assert record.change.diff_sha256 in body and "never merges" in body
    # The token travelled only as a header and reached no persisted state.
    assert all(request.headers["Authorization"] == f"Bearer {TOKEN}" for request in fake.requests)
    assert "PATCH" not in {request.method for request in fake.requests}
    persisted = load_publication(bench.state_root, published.request_id or CYCLE)
    assert persisted == published
    for path in bench.state_root.rglob("*.json"):
        assert TOKEN not in path.read_text(encoding="utf-8"), path
    assert TOKEN not in body
    memories = bench.memory.load_all()
    facts = [item for item in memories if item.status is EpistemicStatus.VALIDATED_FACT]
    assert any("draft pull request #1" in item.content for item in facts)
    assert all(TOKEN not in item.content for item in memories)
    with pytest.raises(ApprovalError, match="already_published"):
        bench.publish(_publisher(fake))
    assert len(fake.pulls) == 1


def test_revise_and_reject_never_publish_and_tampering_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Workbench(tmp_path)
    bench.run_cycle()
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN", TOKEN)
    fake = _fake_for(bench)
    bench.decide(OwnerVerdict.REVISE, "Please also format the tests.")
    with pytest.raises(ApprovalError, match="not_approved: revise"):
        bench.publish(_publisher(fake))
    assert fake.requests == []
    # A second workbench: the owner says ship, but the local patch was altered afterwards.
    other = _Workbench(tmp_path / "second")
    other.run_cycle()
    other.decide(OwnerVerdict.SHIP)
    patch_path = other.run_root / "candidate.patch"
    patch_path.write_bytes(patch_path.read_bytes().replace(b"a + b", b"a - b"))
    with pytest.raises(ApprovalError, match="patch_mismatch"):
        other.publish(_publisher(_fake_for(other)))
    # A decision from someone who is not the configured owner is refused.
    third = _Workbench(tmp_path / "third")
    third.run_cycle()
    third.decide(OwnerVerdict.SHIP)
    with pytest.raises(ApprovalError, match="decision_owner_mismatch"):
        publish_approved_change(
            state_root=third.state_root,
            memory=third.memory,
            owner_id="someone-else",
            cycle="latest",
            publisher=_publisher(_fake_for(third)),
            git=third.git,
            now=LATER,
        )


def test_github_publisher_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _Workbench(tmp_path)
    bench.run_cycle()
    bench.decide(OwnerVerdict.SHIP)
    record = load_cycle_record(bench.state_root)
    assert record.change is not None
    branch = record.change.branch
    assert branch is not None

    # No token: refused before any request.
    monkeypatch.delenv("NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN", raising=False)
    fake = _fake_for(bench)
    with pytest.raises(ApprovalError, match="publish_credentials_missing"):
        bench.publish(_publisher(fake))
    assert fake.requests == []
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN", "bad token\n")
    with pytest.raises(ApprovalError, match="publish_credentials_invalid"):
        bench.publish(_publisher(fake))
    assert fake.requests == []
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN", TOKEN)

    # Rejected credentials: the message names the endpoint stem, never the token.
    fake = _fake_for(bench, unauthorized=True)
    with pytest.raises(ApprovalError, match="publish_credentials_rejected") as failure:
        bench.publish(_publisher(fake))
    assert TOKEN not in str(failure.value) and TOKEN not in str(failure.value.__cause__)

    # The base moved: refused unless the owner explicitly allows it.
    fake = _fake_for(bench, base_head="e" * 40)
    with pytest.raises(ApprovalError, match="publish_base_moved"):
        bench.publish(_publisher(fake))
    assert not fake.blobs and not fake.refs and not fake.pulls
    published = bench.publish(_publisher(fake), allow_moved_base=True)
    assert published.base_head_at_publish == "e" * 40 and fake.pulls
    (bench.state_root / "publications" / f"{published.request_id}.json").unlink()

    # The branch already exists remotely: nothing is written.
    fake = _fake_for(bench, existing_branches=[branch])
    with pytest.raises(ApprovalError, match="publish_branch_exists"):
        bench.publish(_publisher(fake))
    assert not fake.blobs and not fake.pulls

    # The remote tree hashes differently from the local commit: no ref, no pull request.
    fake = _fake_for(bench)
    fake.tree_sha = "d" * 40
    with pytest.raises(ApprovalError, match="publish_tree_mismatch"):
        bench.publish(_publisher(fake))
    assert fake.trees and not fake.commits and not fake.refs and not fake.pulls

    # Drafts unsupported: the branch the publisher created is deleted again.
    fake = _fake_for(bench, draft_supported=False)
    with pytest.raises(ApprovalError, match="publish_draft_unsupported_or_rejected"):
        bench.publish(_publisher(fake))
    assert fake.refs == {} and not fake.pulls
    assert any(request.method == "DELETE" for request in fake.requests)
    request = record.approval_request
    assert request is not None and load_publication(bench.state_root, request.request_id) is None

    # Missing or failed gates are never publishable, whatever the owner said.
    tampered = record.model_copy(
        update={"gates": [item for item in record.gates if item.gate.value != "mypy"]}
    )
    (bench.state_root / "cycles" / "latest.json").write_text(canonical_json(tampered) + "\n")
    with pytest.raises(ApprovalError, match="gates_missing: mypy"):
        bench.publish(_publisher(_fake_for(bench)))


def test_publisher_construction_and_rendering_guard_their_inputs() -> None:
    assert parse_github_repository("https://github.com/Owner/repo.git") == "Owner/repo"
    assert parse_github_repository("git@github.com:owner/repo") == "owner/repo"
    for bad in ("https://gitlab.com/o/r", "owner", "../x", "o/../r", "https://github.com/o"):
        with pytest.raises(PublishError, match="repository_unsupported"):
            parse_github_repository(bad)
    with pytest.raises(PublishError, match="api_base_insecure"):
        GitHubDraftPullRequestPublisher("o/r", api_base="http://api.github.com")
    # Git 2.55 prints UTC author dates with "Z"; older Git prints "+00:00". Same bundle.
    assert normalize_iso8601("2026-09-29T12:00:00Z") == "2026-09-29T12:00:00+00:00"
    assert normalize_iso8601("2026-09-29T12:00:00+00:00") == "2026-09-29T12:00:00+00:00"
    assert normalize_iso8601("2026-09-29T14:00:00+02:00") == "2026-09-29T14:00:00+02:00"
    for bad in ("2026-09-29T12:00:00", "yesterday"):
        with pytest.raises(PublishError, match="bundle_metadata_unreadable"):
            normalize_iso8601(bad)
    candidate = _candidate().model_copy(
        update={"title": "Fix it\nsk-live-0123456789abcdefghijklmnopqrstuvwxyz"}
    )
    title, _ = render_pull_request(
        candidate=candidate,
        change=_change(),
        gates=[],
        request=None,
        decision=None,
        cycle_id=CYCLE,
    )
    assert title == "Fix it", "only the first line becomes the title"
    leaky = _candidate().model_copy(
        update={"title": "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"}
    )
    with pytest.raises(PublishError, match="secret_blocked"):
        render_pull_request(
            candidate=leaky, change=_change(), gates=[], request=None, decision=None, cycle_id=CYCLE
        )


def _change() -> ChangeSummary:
    return ChangeSummary(
        base_sha="1" * 40,
        branch="nexus/software-engineer/x",
        commit_sha="2" * 40,
        changed_files=["README.md"],
        additions=1,
        deletions=1,
        diff_bytes=10,
        diff_sha256=sha256(b"patch").hexdigest(),
        rollback_reference="discard",
    )


# -- executor: autonomous shipping through a publisher -----------------------------------


def test_executor_ships_only_its_own_change_and_withdraws_on_rollback(tmp_path: Path) -> None:
    publisher = RecordingPublisher(clock=lambda: LATER)
    bench = _Workbench(tmp_path, publisher=publisher)
    assert bench.executor.can_ship is True
    outcome = bench.executor.execute(_candidate(), cycle_id=CYCLE, budget=bench.settings.budget)
    assert outcome.change is not None
    foreign = outcome.change.model_copy(update={"diff_bytes": outcome.change.diff_bytes + 1})
    with pytest.raises(ExecutorError, match="not the one this executor produced"):
        bench.executor.ship(foreign, cycle_id=CYCLE)
    with pytest.raises(ExecutorError, match="not the one this executor produced"):
        bench.executor.ship(outcome.change, cycle_id=UUID(int=1))
    shipped = bench.executor.ship(outcome.change, cycle_id=CYCLE)
    publication = shipped.publication
    assert publication is not None and publication.authority == "autonomous_low_risk"
    assert publication.published_by.actor_type is ActorType.AGENT
    assert publication.request_id is None and publication.decision_id is None
    assert "Draft pull request #1" in shipped.rollback_reference
    submission = publisher.submissions[0]
    assert "Opened autonomously" in submission.body and "No human has approved" in submission.body
    assert [item.path for item in submission.bundle.entries] == ["src/pkg/module.py"]
    assert json.loads((bench.run_root / "publication.json").read_text())["pull_request_number"] == 1
    with pytest.raises(ExecutorError, match="no unpublished local branch"):
        bench.executor.ship(shipped, cycle_id=CYCLE)
    with pytest.raises(ExecutorError, match="not the one this executor produced"):
        bench.executor.ship(outcome.change, cycle_id=CYCLE)
    assert len(publisher.submissions) == 1, "a second ship never publishes twice"
    rollback = bench.executor.rollback(shipped, reason="post-ship gate regression")
    assert publisher.withdrawn == [(publication, "post-ship gate regression")]
    assert "closed and branch" in rollback.revert_reference
    # Without a publisher the same executor cannot ship and rolls back locally only.
    plain = PatchForgeExecutor(
        repo_root=bench.repo,
        repository_url=REPOSITORY_URL,
        sandbox=LocalProcessSandbox(allow_local_process=True, clock=lambda: NOW),
        run_root=tmp_path / "plain-runs",
        clock=lambda: NOW,
        git=bench.git,
        commands=_commands,
    )
    assert plain.can_ship is False
    with pytest.raises(ExecutorError, match="not configured"):
        plain.ship(outcome.change, cycle_id=CYCLE)
    assert "nothing was published" in plain.rollback(outcome.change, reason="x").revert_reference


class _SentinelRejectingSandbox:
    """PatchForge's own runs pass; SentinelQA's verification-tree test runs fail."""

    def __init__(self) -> None:
        self.inner = LocalProcessSandbox(allow_local_process=True, clock=lambda: NOW)

    def execute(self, request: SandboxRequest) -> SandboxExecution:
        execution = self.inner.execute(request)
        parts = request.workspace.parts
        if "sentinelqa" in parts and "candidate" in parts and "pytest" in request.command.arguments:
            return execution.model_copy(
                update={
                    "status": SandboxStatus.FAILED,
                    "exit_code": 1,
                    "stdout": b"1 failed in 0.01s\n",
                    "error_code": "command_failed",
                }
            )
        return execution


def test_executor_never_publishes_a_change_sentinelqa_did_not_pass(tmp_path: Path) -> None:
    """The policy already abandons such a change; the publisher boundary re-checks anyway."""

    publisher = RecordingPublisher(clock=lambda: LATER)
    bench = _Workbench(tmp_path, publisher=publisher)
    executor = PatchForgeExecutor(
        repo_root=bench.repo,
        repository_url=REPOSITORY_URL,
        sandbox=_SentinelRejectingSandbox(),
        run_root=tmp_path / "rejected-runs",
        clock=lambda: NOW,
        git=bench.git,
        commands=_commands,
        publisher=publisher,
    )
    outcome = executor.execute(_candidate(), cycle_id=CYCLE, budget=bench.settings.budget)
    assert outcome.change is not None
    statuses = {item.gate: item.status for item in outcome.gates}
    assert statuses[ValidationGate.PYTEST_FULL] is GateStatus.PASSED
    assert statuses[ValidationGate.SENTINEL_REVIEW] is GateStatus.FAILED
    with pytest.raises(ExecutorError, match="including SentinelQA"):
        executor.ship(outcome.change, cycle_id=CYCLE)
    assert publisher.submissions == []


def test_publisher_refusal_is_a_typed_executor_error(tmp_path: Path) -> None:
    publisher = RecordingPublisher(fail_with="branch_exists", clock=lambda: LATER)
    bench = _Workbench(tmp_path, publisher=publisher)
    outcome = bench.executor.execute(_candidate(), cycle_id=CYCLE, budget=bench.settings.budget)
    assert outcome.change is not None
    with pytest.raises(ExecutorError, match="Publishing refused: branch_exists"):
        bench.executor.ship(outcome.change, cycle_id=CYCLE)
    assert not (bench.run_root / "publication.json").exists()
    assert publisher.submissions == []


def test_recording_publisher_honours_base_movement(tmp_path: Path) -> None:
    publisher = RecordingPublisher(base_head="a" * 40, clock=lambda: LATER)
    bench = _Workbench(tmp_path)
    bench.run_cycle()
    change = load_cycle_record(bench.state_root).change
    assert change is not None and change.commit_sha is not None
    bundle = bundle_from_branch(
        bench.git,
        bench.run_root / "branch",
        base_sha=change.base_sha,
        commit_sha=change.commit_sha,
        patch=(bench.run_root / "candidate.patch").read_bytes(),
        work_root=tmp_path / "work",
    )
    submission = PublishSubmission(
        bundle=bundle,
        branch="nexus/software-engineer/x",
        title="t",
        body="b",
        cycle_id=CYCLE,
        diff_sha256=change.diff_sha256,
        published_by=agent_publisher_identity(),
        authority="autonomous_low_risk",
    )
    with pytest.raises(PublishError, match="base_moved"):
        publisher.publish(submission)
    moved = replace(submission, allow_moved_base=True)
    assert publisher.publish(moved).base_head_at_publish == "a" * 40


# -- CLI ---------------------------------------------------------------------------------


def test_cli_decide_and_publish_are_explicit_owner_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in list(os.environ):
        if key.startswith("NEXUS_SOFTWARE_ENGINEER_"):
            monkeypatch.delenv(key)
    bench = _Workbench(tmp_path)
    bench.run_cycle()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_OWNER_ID", bench.settings.owner_id)
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_REPOSITORY_URL", REPOSITORY_URL)
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_MEMORY_PATH", str(bench.settings.memory_path))
    state = ["--state-root", str(bench.state_root)]
    assert cli.main(["preflight"]) == 0
    assert "github_token_present=False publish_from_cycle=False" in capsys.readouterr().out
    # Publishing is an engineer action: disabled means refused, even after a decision.
    assert cli.main([*state, "decide", "--verdict", "ship", "--reason", "fine"]) == 0
    assert "verdict=ship" in capsys.readouterr().out
    assert cli.main([*state, "publish"]) == 2
    assert "Set NEXUS_SOFTWARE_ENGINEER_ENABLED" in capsys.readouterr().err
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_ENABLED", "true")
    assert cli.main([*state, "publish"]) == 2
    assert "publish_credentials_missing" in capsys.readouterr().err
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN", TOKEN)
    fake = _fake_for(bench)
    monkeypatch.setattr(cli, "_publisher", lambda settings: _publisher(fake))
    assert cli.main([*state, "publish"]) == 0
    out = capsys.readouterr().out
    assert "published draft pull request #1" in out and TOKEN not in out
    assert cli.main([*state, "publish"]) == 2
    assert "already_published" in capsys.readouterr().err
    assert cli.main([*state, "decide", "--verdict", "reject", "--reason", "x"]) == 2
    assert "already_decided" in capsys.readouterr().err
    # A daily cycle never gets a publisher unless every explicit condition holds.
    settings = SoftwareEngineerSettings(_env_file=None)  # type: ignore[call-arg]
    assert cli._cycle_publisher(settings) is None
    autonomous = settings.model_copy(update={"mode": CycleMode.AUTONOMOUS_LOW_RISK})
    assert cli._cycle_publisher(autonomous) is None
    opted_in = autonomous.model_copy(update={"publish_from_cycle": True})
    assert cli._cycle_publisher(opted_in) is not None
    monkeypatch.delenv("NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN")
    assert cli._cycle_publisher(opted_in) is None
