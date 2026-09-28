"""Disposable PatchForge workspace and hardened Git authority tests."""

import os
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from nexus.atlas.models import SourceRevision
from nexus.patchforge.canonical import canonical_json, canonical_sha256
from nexus.patchforge.models import EngineeringTask
from nexus.patchforge.policy import (
    CommandPurpose,
    RepositoryProfile,
    SandboxCommand,
    SandboxPolicy,
)
from nexus.patchforge.workspace import (
    GitCommandError,
    GitOutputLimitError,
    GitRunner,
    WorkspaceError,
    WorkspaceHandle,
    WorkspaceIntegrityError,
    WorkspaceManager,
)

NOW = datetime(2026, 9, 28, 2, tzinfo=UTC)


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
    )
    return result.stdout.decode("utf-8").strip()


def _repository(path: Path) -> tuple[Path, str]:
    path.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(path)],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
    )
    _git(path, "config", "user.name", "PatchForge Fixture")
    _git(path, "config", "user.email", "fixture@example.invalid")
    (path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _git(path, "add", ".")
    _git(path, "commit", "-m", "fixture: initial")
    return path, _git(path, "rev-parse", "HEAD")


def _profile() -> RepositoryProfile:
    return RepositoryProfile(
        profile_id="fixture.python",
        profile_version=1,
        repository_url="https://example.invalid/fixtures/calculator",
        sandbox=SandboxPolicy(
            image=f"python@sha256:{'a' * 64}",
            run_as_user="10001:10001",
            cpu_limit_millis=1000,
            memory_limit_mb=256,
            pids_limit=64,
            default_timeout_seconds=60,
            max_output_bytes=100_000,
        ),
        commands={
            CommandPurpose.FULL_TEST_SUITE: SandboxCommand(
                executable="python",
                arguments=["-m", "pytest"],
                timeout_seconds=60,
                max_output_bytes=100_000,
            )
        },
        test_path_prefixes=["tests"],
    )


def _task(source_sha: str, profile: RepositoryProfile | None = None) -> EngineeringTask:
    selected_profile = profile or _profile()
    return EngineeringTask(
        task_id=UUID(int=2),
        atlas_job_id=UUID(int=3),
        title="Fix subtraction",
        instructions="Correct subtraction and add a regression test.",
        acceptance_criteria=["Subtraction returns the expected result."],
        source=SourceRevision(
            repository_url="https://example.invalid/fixtures/calculator",
            commit_sha=source_sha,
        ),
        repository_profile_id=selected_profile.profile_id,
        repository_profile_sha256=canonical_sha256(selected_profile),
        scope_paths=["calculator.py", "tests"],
        created_at=NOW,
    )


def test_provision_uses_exact_sha_and_separates_git_from_execution_tree(tmp_path: Path) -> None:
    source, first_sha = _repository(tmp_path / "source")
    (source / "calculator.py").write_text(
        "def add(a, b):\n    return a + b + 1\n",
        encoding="utf-8",
    )
    _git(source, "add", ".")
    _git(source, "commit", "-m", "fixture: later change")

    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    profile = _profile()
    run_id = UUID(int=10)
    handle = manager.provision(
        _task(first_sha, profile),
        profile,
        run_id,
        source,
        lease_duration=timedelta(minutes=5),
    )

    assert handle.record.source_sha == first_sha
    assert handle.record.branch_name == f"nexus/patchforge/{run_id.hex}"
    assert "return a + b + 1" not in (handle.worktree / "calculator.py").read_text(encoding="utf-8")
    assert not (handle.worktree / ".git").exists()
    assert handle.git_directory.is_dir()
    assert manager.status_porcelain(handle) == b""


def test_diff_is_deterministic_and_includes_tracked_untracked_and_binary_files(
    tmp_path: Path,
) -> None:
    source, source_sha = _repository(tmp_path / "source")
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    profile = _profile()
    handle = manager.provision(
        _task(source_sha, profile),
        profile,
        UUID(int=11),
        source,
        lease_duration=timedelta(minutes=5),
    )
    (handle.worktree / "calculator.py").write_bytes(
        b"def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"
    )
    (handle.worktree / "notes.txt").write_bytes(b"fixture note\n")
    (handle.worktree / "asset.bin").write_bytes(b"\x00\x01\x02")

    first = manager.inspect_diff(handle)
    second = manager.inspect_diff(handle)

    assert first == second
    assert first.base_sha == source_sha
    assert first.changed_files == ("asset.bin", "calculator.py", "notes.txt")
    assert first.binary_files == ("asset.bin",)
    assert first.additions >= 4
    assert first.deletions == 0
    assert len(first.diff_sha256) == 64
    assert b"subtract" in first.patch


def test_cleanup_is_idempotent_but_refuses_unmanaged_directories(tmp_path: Path) -> None:
    source, source_sha = _repository(tmp_path / "source")
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    profile = _profile()
    run_id = UUID(int=12)
    manager.provision(
        _task(source_sha, profile),
        profile,
        run_id,
        source,
        lease_duration=timedelta(minutes=5),
    )
    assert manager.cleanup(run_id) is True
    assert manager.cleanup(run_id) is False

    unmanaged_id = UUID(int=13)
    unmanaged = manager.root / unmanaged_id.hex
    unmanaged.mkdir()
    (unmanaged / "keep.txt").write_text("do not delete", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="marker"):
        manager.cleanup(unmanaged_id)
    assert unmanaged.exists()


def test_cleanup_removes_readonly_runtime_git_objects(tmp_path: Path) -> None:
    source, source_sha = _repository(tmp_path / "source")
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    profile = _profile()
    run_id = UUID(int=120)
    handle = manager.provision(
        _task(source_sha, profile),
        profile,
        run_id,
        source,
        lease_duration=timedelta(minutes=5),
    )
    object_file = next(
        path for path in (handle.git_directory / "objects").rglob("*") if path.is_file()
    )
    object_file.chmod(stat.S_IREAD)

    assert manager.cleanup(run_id) is True
    assert not handle.root.exists()


def test_expired_lease_reaper_removes_only_verified_workspaces(tmp_path: Path) -> None:
    source, source_sha = _repository(tmp_path / "source")
    now = [NOW]
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: now[0])
    profile = _profile()
    expired_id = UUID(int=14)
    active_id = UUID(int=15)
    manager.provision(
        _task(source_sha, profile),
        profile,
        expired_id,
        source,
        lease_duration=timedelta(seconds=10),
    )
    active = manager.provision(
        _task(source_sha, profile),
        profile,
        active_id,
        source,
        lease_duration=timedelta(minutes=10),
    )
    unmanaged_id = UUID(int=16)
    (manager.root / unmanaged_id.hex).mkdir()
    now[0] += timedelta(seconds=11)

    assert manager.reap_orphans() == [expired_id]
    assert not (manager.root / expired_id.hex).exists()
    assert active.root.exists()
    assert (manager.root / unmanaged_id.hex).exists()


def test_lease_renewal_is_durable(tmp_path: Path) -> None:
    source, source_sha = _repository(tmp_path / "source")
    now = [NOW]
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: now[0])
    profile = _profile()
    handle = manager.provision(
        _task(source_sha, profile),
        profile,
        UUID(int=17),
        source,
        lease_duration=timedelta(seconds=10),
    )
    now[0] += timedelta(seconds=5)
    renewed = manager.renew_lease(handle, timedelta(minutes=2))
    now[0] += timedelta(seconds=10)
    assert manager.reap_orphans() == []
    assert renewed.root.exists()


def test_failed_provision_removes_only_its_managed_workspace(tmp_path: Path) -> None:
    source, _ = _repository(tmp_path / "source")
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    profile = _profile()
    run_id = UUID(int=18)
    with pytest.raises(WorkspaceError):
        manager.provision(
            _task("f" * 40, profile),
            profile,
            run_id,
            source,
            lease_duration=timedelta(minutes=5),
        )
    assert not (manager.root / run_id.hex).exists()


def test_provision_rejects_a_repository_profile_hash_mismatch(tmp_path: Path) -> None:
    source, source_sha = _repository(tmp_path / "source")
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    profile = _profile()
    changed_profile = profile.model_copy(update={"profile_version": 2})
    with pytest.raises(WorkspaceError, match="profile hash"):
        manager.provision(
            _task(source_sha, profile),
            changed_profile,
            UUID(int=20),
            source,
            lease_duration=timedelta(minutes=5),
        )
    assert not (manager.root / UUID(int=20).hex).exists()


def test_cleanup_refuses_a_tampered_workspace_marker(tmp_path: Path) -> None:
    source, source_sha = _repository(tmp_path / "source")
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    profile = _profile()
    run_id = UUID(int=21)
    handle = manager.provision(
        _task(source_sha, profile),
        profile,
        run_id,
        source,
        lease_duration=timedelta(minutes=5),
    )
    tampered = handle.record.model_copy(update={"workspace_id": UUID(int=999)})
    (handle.root / "workspace.json").write_text(
        canonical_json(tampered),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(WorkspaceError, match="identity"):
        manager.cleanup(run_id)
    assert handle.root.exists()


def test_diff_capture_enforces_the_configured_output_bound(tmp_path: Path) -> None:
    source, source_sha = _repository(tmp_path / "source")
    manager = WorkspaceManager(
        tmp_path / "workspaces",
        clock=lambda: NOW,
        max_diff_bytes=8,
    )
    profile = _profile()
    handle = manager.provision(
        _task(source_sha, profile),
        profile,
        UUID(int=22),
        source,
        lease_duration=timedelta(minutes=5),
    )
    (handle.worktree / "calculator.py").write_bytes(b"changed\n")
    with pytest.raises(GitOutputLimitError, match="output bytes"):
        manager.inspect_diff(handle)


def test_source_repository_cannot_be_nested_under_workspace_root(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    profile = _profile()
    source, source_sha = _repository(manager.root / "source")
    with pytest.raises(WorkspaceError, match="inside the workspace root"):
        manager.provision(
            _task(source_sha, profile),
            profile,
            UUID(int=19),
            source,
            lease_duration=timedelta(minutes=5),
        )


def test_git_runner_enforces_combined_output_bound(tmp_path: Path) -> None:
    runner = GitRunner(tmp_path / "runtime")
    with pytest.raises(GitOutputLimitError, match="output bytes"):
        runner.run(["help", "-a"], output_limit=1)
    with pytest.raises(WorkspaceError, match="bounds must be positive"):
        runner.run(["--version"], output_limit=0)


@pytest.mark.parametrize("change", ["modify", "delete"])
def test_changed_tracked_ignore_rules_fail_closed(tmp_path: Path, change: str) -> None:
    source, _ = _repository(tmp_path / "source")
    (source / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _git(source, "add", ".gitignore")
    _git(source, "commit", "-m", "fixture: ignore logs")
    source_sha = _git(source, "rev-parse", "HEAD")
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    profile = _profile()
    handle = manager.provision(
        _task(source_sha, profile),
        profile,
        UUID(int=12),
        source,
        lease_duration=timedelta(minutes=5),
    )
    assert manager.inspect_diff(handle).changed_files == ()
    ignore_file = handle.worktree / ".gitignore"
    if change == "modify":
        ignore_file.write_text("*.log\n*.py\n", encoding="utf-8")
    else:
        ignore_file.unlink()
    with pytest.raises(WorkspaceIntegrityError, match="ignore or attribute rules"):
        manager.inspect_diff(handle)
    with pytest.raises(WorkspaceIntegrityError, match="ignore or attribute rules"):
        manager.status_porcelain(handle)


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot create colon-containing filenames")
def test_diff_rejects_paths_that_are_not_portable(tmp_path: Path) -> None:
    source, source_sha = _repository(tmp_path / "source")
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: NOW)
    profile = _profile()
    handle = manager.provision(
        _task(source_sha, profile),
        profile,
        UUID(int=13),
        source,
        lease_duration=timedelta(minutes=5),
    )
    (handle.worktree / "a:b.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(WorkspaceIntegrityError, match="portable"):
        manager.inspect_diff(handle)


def test_active_verification_rejects_an_expired_lease(tmp_path: Path) -> None:
    source, source_sha = _repository(tmp_path / "source")
    clock = [NOW]
    manager = WorkspaceManager(tmp_path / "workspaces", clock=lambda: clock[0])
    profile = _profile()
    handle = manager.provision(
        _task(source_sha, profile),
        profile,
        UUID(int=14),
        source,
        lease_duration=timedelta(minutes=5),
    )
    assert manager.verify_active(handle) == handle
    clock[0] = NOW + timedelta(minutes=5)
    with pytest.raises(WorkspaceError, match="lease has expired"):
        manager.verify_active(handle)


def _provisioned(
    tmp_path: Path, name: str, run: int
) -> tuple[WorkspaceManager, WorkspaceHandle, str]:
    source, source_sha = _repository(tmp_path / f"{name}-source")
    manager = WorkspaceManager(tmp_path / f"{name}-workspaces", clock=lambda: NOW)
    profile = _profile()
    handle = manager.provision(
        _task(source_sha, profile),
        profile,
        UUID(int=run),
        source,
        lease_duration=timedelta(minutes=5),
    )
    return manager, handle, source_sha


def _mutate(worktree: Path) -> None:
    (worktree / "calculator.py").write_bytes(b"def add(a, b):\n    return b + a\n")
    (worktree / "asset.bin").write_bytes(b"\x00\x01\x02")
    (worktree / "notes.txt").write_bytes(b"new\n")


def test_proposal_commit_is_deterministic_and_matches_the_worktree_diff(
    tmp_path: Path,
) -> None:
    # Both workspaces share one source repository: its commit SHA depends on wall-clock
    # time, so two separately created fixtures can legitimately differ.
    source, source_sha = _repository(tmp_path / "source")
    profile = _profile()
    commits = []
    for name in ("first", "second"):
        manager = WorkspaceManager(tmp_path / f"{name}-workspaces", clock=lambda: NOW)
        handle = manager.provision(
            _task(source_sha, profile),
            profile,
            UUID(int=21),
            source,
            lease_duration=timedelta(minutes=5),
        )
        _mutate(handle.worktree)
        proposal = manager.propose_commit(handle, message="PatchForge proposal", committed_at=NOW)
        assert proposal.diff == manager.inspect_diff(handle)
        assert proposal.diff.base_sha == source_sha
        assert proposal.diff.changed_files == ("asset.bin", "calculator.py", "notes.txt")
        parent = _git(handle.git_directory, "rev-parse", f"{proposal.commit_sha}^")
        assert parent == source_sha
        author = _git(
            handle.git_directory, "log", "-1", "--format=%an <%ae> %at", proposal.commit_sha
        )
        assert (
            author
            == f"PatchForge Runtime <patchforge-runtime@nexus.invalid> {int(NOW.timestamp())}"
        )
        assert not (handle.root / "control" / "proposal.index").exists()
        commits.append(proposal.commit_sha)
    assert commits[0] == commits[1]


def test_proposal_requires_changes_and_an_aware_timestamp(tmp_path: Path) -> None:
    manager, handle, _ = _provisioned(tmp_path, "empty", 22)
    assert isinstance(handle, WorkspaceHandle)
    with pytest.raises(WorkspaceError, match="no changes"):
        manager.propose_commit(handle, message="PatchForge proposal", committed_at=NOW)
    _mutate(handle.worktree)
    with pytest.raises(WorkspaceError, match="timezone-aware"):
        manager.propose_commit(
            handle, message="PatchForge proposal", committed_at=NOW.replace(tzinfo=None)
        )


def test_git_runner_rejects_unlisted_environment_overrides(tmp_path: Path) -> None:
    runner = GitRunner(tmp_path / "runtime")
    with pytest.raises(GitCommandError, match="overrides"):
        runner.run(["--version"], environment_overrides={"GIT_DIR": "/elsewhere"})
    assert runner.run(
        ["--version"], environment_overrides={"GIT_AUTHOR_NAME": "PatchForge Runtime"}
    ).stdout.startswith(b"git version")
