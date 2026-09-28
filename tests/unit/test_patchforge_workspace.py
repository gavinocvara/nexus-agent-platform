"""Disposable PatchForge workspace and hardened Git authority tests."""

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
    GitOutputLimitError,
    GitRunner,
    WorkspaceError,
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
