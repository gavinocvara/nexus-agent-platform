"""Disposable PatchForge workspaces with runtime-owned Git authority."""

import os
import shutil
import stat
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, TypeAdapter, ValidationError

from nexus.atlas.models import CommitSha, StrictModel
from nexus.patchforge.canonical import canonical_json, canonical_sha256
from nexus.patchforge.models import EngineeringTask, RepositoryPath
from nexus.patchforge.policy import RepositoryProfile

Clock = Callable[[], datetime]
WORKSPACE_MARKER = "workspace.json"
GIT_CONTROL_FILES = frozenset({".gitignore", ".gitattributes"})
_CONTROL_FILE_PATHSPECS = tuple(f":(top,glob)**/{name}" for name in sorted(GIT_CONTROL_FILES))
# Tools write a self-ignoring .gitignore into these caches. Such a file only affects its own
# directory, which is neither importable nor collected by pytest, so it cannot hide changes.
TOOL_CACHE_DIRECTORIES = frozenset({".pytest_cache", ".mypy_cache", ".ruff_cache"})
_REPOSITORY_PATHS: TypeAdapter[list[RepositoryPath]] = TypeAdapter(list[RepositoryPath])


class WorkspaceError(RuntimeError):
    """A workspace cannot be provisioned, inspected, or removed safely."""


class GitCommandError(WorkspaceError):
    """A bounded runtime-owned Git command failed."""


class GitTimeoutError(GitCommandError):
    """A Git command exceeded its explicit timeout."""


class GitOutputLimitError(GitCommandError):
    """A Git command exceeded its bounded captured output."""


class WorkspaceIntegrityError(WorkspaceError):
    """The workspace cannot be attested faithfully and must fail closed."""


class WorkspaceState(StrEnum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"


class WorkspaceRecord(StrictModel):
    schema_version: Literal[1] = 1
    workspace_id: UUID
    run_id: UUID
    task_id: UUID
    source_sha: CommitSha
    branch_name: str = Field(min_length=1, max_length=255)
    state: WorkspaceState
    created_at: AwareDatetime
    lease_expires_at: AwareDatetime


class WorkspaceHandle(StrictModel):
    record: WorkspaceRecord
    root: Path
    worktree: Path
    git_directory: Path


@dataclass(frozen=True, slots=True)
class WorkspaceDiff:
    base_sha: str
    patch: bytes
    diff_sha256: str
    changed_files: tuple[str, ...]
    additions: int
    deletions: int
    binary_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    stdout: bytes
    stderr: bytes

    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="strict")

    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")


class _BoundedCapture:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._size = 0
        self._lock = threading.Lock()
        self._chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
        self.truncated = False

    def append(self, stream: str, chunk: bytes) -> None:
        with self._lock:
            remaining = self._limit - self._size
            if remaining <= 0:
                self.truncated = True
                return
            accepted = chunk[:remaining]
            self._chunks[stream].append(accepted)
            self._size += len(accepted)
            if len(accepted) != len(chunk):
                self.truncated = True

    def value(self, stream: str) -> bytes:
        return b"".join(self._chunks[stream])


class GitRunner:
    """No-shell Git runner with sanitized configuration, timeout, and output bounds."""

    def __init__(
        self,
        runtime_directory: Path,
        *,
        default_timeout_seconds: int = 30,
        default_output_limit: int = 2_000_000,
    ) -> None:
        executable = shutil.which("git")
        if executable is None:
            raise WorkspaceError("Git executable is unavailable")
        self.executable = str(Path(executable).resolve())
        self.default_timeout_seconds = default_timeout_seconds
        self.default_output_limit = default_output_limit
        self.runtime_directory = runtime_directory.resolve()
        self.runtime_directory.mkdir(parents=True, exist_ok=True)
        self._global_config = self.runtime_directory / "empty-gitconfig"
        self._global_config.touch(exist_ok=True)

    def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: int | None = None,
        output_limit: int | None = None,
        allowed_exit_codes: frozenset[int] = frozenset({0}),
    ) -> GitCommandResult:
        if not arguments or any("\x00" in value for value in arguments):
            raise GitCommandError("Git command arguments are invalid")
        timeout = self.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        limit = self.default_output_limit if output_limit is None else output_limit
        if timeout < 1 or limit < 1:
            raise GitCommandError("Git command bounds must be positive")
        environment = self._environment()
        command = [self.executable, *arguments]
        creation_flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        )
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=os.name != "nt",
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise GitCommandError("Git command could not start") from exc
        if process.stdout is None or process.stderr is None:
            process.kill()
            raise GitCommandError("Git output pipes are unavailable")
        capture = _BoundedCapture(limit)
        threads = [
            threading.Thread(
                target=self._drain,
                args=(process.stdout, capture, "stdout"),
                daemon=True,
            ),
            threading.Thread(
                target=self._drain,
                args=(process.stderr, capture, "stderr"),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            return_code = process.wait()
        finally:
            for thread in threads:
                thread.join(timeout=5)
        stdout = capture.value("stdout")
        stderr = capture.value("stderr")
        operation = arguments[0]
        if timed_out:
            raise GitTimeoutError(f"Git {operation} exceeded {timeout} seconds")
        if capture.truncated:
            raise GitOutputLimitError(f"Git {operation} exceeded {limit} output bytes")
        if return_code not in allowed_exit_codes:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise GitCommandError(f"Git {operation} failed with exit {return_code}: {detail}")
        return GitCommandResult(stdout=stdout, stderr=stderr)

    @staticmethod
    def _drain(stream: BinaryIO, capture: _BoundedCapture, name: str) -> None:
        while chunk := stream.read(65_536):
            capture.append(name, chunk)

    def _environment(self) -> dict[str, str]:
        allowed = (
            "COMSPEC",
            "PATH",
            "PATHEXT",
            "SYSTEMDRIVE",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "WINDIR",
        )
        environment = {key: os.environ[key] for key in allowed if key in os.environ}
        environment.update(
            {
                "GIT_CONFIG_GLOBAL": str(self._global_config),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "Never",
                "LANG": "C",
                "LC_ALL": "C",
            }
        )
        return environment


class WorkspaceManager:
    """Provision and reap disposable trees while keeping Git metadata outside them."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Clock | None = None,
        git_runner: GitRunner | None = None,
        max_diff_bytes: int = 10_000_000,
    ) -> None:
        if max_diff_bytes < 1:
            raise ValueError("Workspace diff bound must be positive")
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise WorkspaceError("Workspace root cannot be a symbolic link")
        self.root = root.resolve()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.git = git_runner or GitRunner(self.root / ".runtime")
        self.max_diff_bytes = max_diff_bytes

    def provision(
        self,
        task: EngineeringTask,
        profile: RepositoryProfile,
        run_id: UUID,
        source_repository: Path,
        *,
        lease_duration: timedelta,
    ) -> WorkspaceHandle:
        now = self._now()
        if lease_duration <= timedelta(0):
            raise WorkspaceError("Workspace lease duration must be positive")
        if task.repository_profile_id != profile.profile_id:
            raise WorkspaceError("Task repository profile identity does not match")
        if task.repository_profile_sha256 != canonical_sha256(profile):
            raise WorkspaceError("Task repository profile hash does not match")
        if str(task.source.repository_url) != str(profile.repository_url):
            raise WorkspaceError("Task repository URL does not match its profile")
        source = source_repository.resolve(strict=True)
        if self._is_within(source, self.root):
            raise WorkspaceError("Source repository cannot be inside the workspace root")
        self.git.run(["-C", str(source), "rev-parse", "--is-inside-work-tree"])
        target = self._workspace_path(run_id)
        if target.exists():
            raise WorkspaceError(f"Workspace already exists: {run_id}")
        target.mkdir()
        record = WorkspaceRecord(
            workspace_id=run_id,
            run_id=run_id,
            task_id=task.task_id,
            source_sha=task.source.commit_sha,
            branch_name=f"nexus/patchforge/{run_id.hex}",
            state=WorkspaceState.PROVISIONING,
            created_at=now,
            lease_expires_at=now + lease_duration,
        )
        self._write_record(target, record)
        control = target / "control"
        worktree = target / "worktree"
        git_directory = control / "repository.git"
        control.mkdir()
        worktree.mkdir()
        try:
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
            self._git(
                git_directory,
                worktree,
                ["rev-parse", "--verify", f"{task.source.commit_sha}^{{commit}}"],
            )
            self._git(
                git_directory,
                worktree,
                ["branch", record.branch_name, task.source.commit_sha],
            )
            self._git(
                git_directory,
                worktree,
                ["symbolic-ref", "HEAD", f"refs/heads/{record.branch_name}"],
            )
            self._git(git_directory, worktree, ["checkout", "--force", record.branch_name])
            head = self._git(git_directory, worktree, ["rev-parse", "HEAD"]).stdout_text().strip()
            if head != task.source.commit_sha:
                raise WorkspaceError("Workspace HEAD does not match the requested source SHA")
            if (worktree / ".git").exists():
                raise WorkspaceError("Execution worktree unexpectedly contains Git metadata")
            active = record.model_copy(update={"state": WorkspaceState.ACTIVE})
            self._write_record(target, active)
            return WorkspaceHandle(
                record=active,
                root=target,
                worktree=worktree,
                git_directory=git_directory,
            )
        except Exception:
            self._safe_remove(target, expected_id=run_id)
            raise

    def inspect_diff(self, handle: WorkspaceHandle) -> WorkspaceDiff:
        current = self._verify_handle(handle)
        self._verify_ignore_rules(current)
        self._git(current.git_directory, current.worktree, ["add", "--intent-to-add", "--", "."])
        patch = self._git(
            current.git_directory,
            current.worktree,
            ["diff", "--binary", "--no-ext-diff", "--full-index", "HEAD", "--"],
            output_limit=self.max_diff_bytes,
        ).stdout
        names = self._git(
            current.git_directory,
            current.worktree,
            ["diff", "--name-only", "-z", "HEAD", "--"],
        ).stdout
        changed = tuple(sorted(self._decode_paths(names)))
        numstat = self._git(
            current.git_directory,
            current.worktree,
            ["diff", "--numstat", "HEAD", "--"],
        ).stdout_text()
        additions, deletions, binary_count = self._parse_numstat(numstat)
        binary_files: tuple[str, ...] = ()
        if binary_count:
            binary = self._git(
                current.git_directory,
                current.worktree,
                ["diff", "--numstat", "-z", "HEAD", "--"],
            ).stdout
            binary_files = tuple(sorted(self._binary_paths(binary)))
        return WorkspaceDiff(
            base_sha=current.record.source_sha,
            patch=patch,
            diff_sha256=sha256(patch).hexdigest(),
            changed_files=changed,
            additions=additions,
            deletions=deletions,
            binary_files=binary_files,
        )

    def status_porcelain(self, handle: WorkspaceHandle) -> bytes:
        current = self._verify_handle(handle)
        self._verify_ignore_rules(current)
        return self._git(
            current.git_directory,
            current.worktree,
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        ).stdout

    def verify_active(self, handle: WorkspaceHandle) -> WorkspaceHandle:
        """Verify durable identity, managed layout, and an unexpired lease."""
        current = self._verify_handle(handle)
        if current.record.lease_expires_at <= self._now():
            raise WorkspaceError("Workspace lease has expired")
        return current

    def is_ignored(self, handle: WorkspaceHandle, path: RepositoryPath) -> bool:
        """Return whether Git ignore rules would hide an untracked file at this path."""
        current = self._verify_handle(handle)
        output = self._git(
            current.git_directory,
            current.worktree,
            ["check-ignore", "--", path],
            allowed_exit_codes=frozenset({0, 1}),
        ).stdout
        return bool(output)

    def renew_lease(self, handle: WorkspaceHandle, lease_duration: timedelta) -> WorkspaceHandle:
        current = self._verify_handle(handle)
        now = self._now()
        if lease_duration <= timedelta(0):
            raise WorkspaceError("Workspace lease duration must be positive")
        updated = current.record.model_copy(update={"lease_expires_at": now + lease_duration})
        self._write_record(current.root, updated)
        return current.model_copy(update={"record": updated})

    def cleanup(self, workspace_id: UUID) -> bool:
        target = self._workspace_path(workspace_id)
        if not target.exists():
            return False
        self._safe_remove(target, expected_id=workspace_id)
        return True

    def reap_orphans(self) -> list[UUID]:
        now = self._now()
        reaped: list[UUID] = []
        for candidate in sorted(self.root.iterdir(), key=lambda path: path.name):
            if not candidate.is_dir() or candidate.is_symlink() or len(candidate.name) != 32:
                continue
            try:
                workspace_id = UUID(hex=candidate.name)
                record = self._read_record(candidate)
            except (ValueError, WorkspaceError):
                continue
            if record.workspace_id != workspace_id or record.lease_expires_at > now:
                continue
            self._safe_remove(candidate, expected_id=workspace_id)
            reaped.append(workspace_id)
        return reaped

    def _verify_handle(self, handle: WorkspaceHandle) -> WorkspaceHandle:
        expected_root = self._workspace_path(handle.record.workspace_id)
        if handle.root.resolve() != expected_root:
            raise WorkspaceError("Workspace handle root is outside the managed directory")
        record = self._read_record(expected_root)
        if record != handle.record or record.state is not WorkspaceState.ACTIVE:
            raise WorkspaceError("Workspace handle does not match durable state")
        expected_worktree = expected_root / "worktree"
        expected_git = expected_root / "control" / "repository.git"
        if (
            handle.worktree.resolve() != expected_worktree
            or handle.git_directory.resolve() != expected_git
        ):
            raise WorkspaceError("Workspace handle paths do not match the managed layout")
        if (expected_worktree / ".git").exists():
            raise WorkspaceError("Execution worktree contains forbidden Git metadata")
        return handle

    def _verify_ignore_rules(self, handle: WorkspaceHandle) -> None:
        """Fail closed when ignore or attribute rules differ from the source commit.

        Status and diff honor these files, so changing them could hide worktree changes.
        Untracked control files are listed with every ignore rule disabled. Only a tool
        cache's own ``.gitignore`` is tolerated.
        """
        listed = self._git(
            handle.git_directory,
            handle.worktree,
            ["ls-files", "-z", "--others", "--", *_CONTROL_FILE_PATHSPECS],
        ).stdout
        untracked = [
            path
            for path in listed.split(b"\0")
            if path and not self._is_tool_cache_ignore_file(path)
        ]
        changed = self._git(
            handle.git_directory,
            handle.worktree,
            ["diff", "--name-only", "-z", "HEAD", "--", *_CONTROL_FILE_PATHSPECS],
        ).stdout
        if untracked or changed:
            raise WorkspaceIntegrityError(
                "Workspace Git ignore or attribute rules differ from the source commit"
            )

    @staticmethod
    def _is_tool_cache_ignore_file(path: bytes) -> bool:
        parts = path.split(b"/")
        return (
            len(parts) >= 2
            and parts[-1] == b".gitignore"
            and parts[-2].decode("utf-8", errors="replace") in TOOL_CACHE_DIRECTORIES
        )

    def _git(
        self,
        git_directory: Path,
        worktree: Path,
        arguments: Sequence[str],
        *,
        output_limit: int | None = None,
        allowed_exit_codes: frozenset[int] = frozenset({0}),
    ) -> GitCommandResult:
        return self.git.run(
            [
                f"--git-dir={git_directory}",
                f"--work-tree={worktree}",
                "-c",
                "core.hooksPath=NUL" if os.name == "nt" else "core.hooksPath=/dev/null",
                "-c",
                "core.autocrlf=false",
                *arguments,
            ],
            output_limit=output_limit,
            allowed_exit_codes=allowed_exit_codes,
        )

    def _workspace_path(self, workspace_id: UUID) -> Path:
        target = (self.root / workspace_id.hex).resolve()
        if target.parent != self.root:
            raise WorkspaceError("Workspace path escaped the configured root")
        return target

    def _safe_remove(self, target: Path, *, expected_id: UUID) -> None:
        resolved = target.resolve()
        if resolved.parent != self.root or resolved.name != expected_id.hex or target.is_symlink():
            raise WorkspaceError("Refusing to remove an unmanaged workspace path")
        record = self._read_record(resolved)
        if record.workspace_id != expected_id or record.run_id != expected_id:
            raise WorkspaceError("Workspace marker identity does not match its directory")

        def remove_readonly(
            function: Callable[..., object],
            path: str,
            exception: BaseException,
        ) -> None:
            candidate = Path(path).resolve()
            if not isinstance(exception, PermissionError) or not candidate.is_relative_to(resolved):
                raise exception
            os.chmod(candidate, stat.S_IREAD | stat.S_IWRITE)
            function(path)

        shutil.rmtree(resolved, onexc=remove_readonly)

    def _write_record(self, target: Path, record: WorkspaceRecord) -> None:
        marker = target / WORKSPACE_MARKER
        temporary = target / f".{WORKSPACE_MARKER}.tmp"
        temporary.write_text(canonical_json(record), encoding="utf-8", newline="\n")
        os.replace(temporary, marker)

    @staticmethod
    def _read_record(target: Path) -> WorkspaceRecord:
        marker = target / WORKSPACE_MARKER
        try:
            return WorkspaceRecord.model_validate_json(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError) as exc:
            raise WorkspaceError("Workspace marker is missing or malformed") from exc

    @staticmethod
    def _decode_paths(payload: bytes) -> list[RepositoryPath]:
        try:
            values = [
                item.decode("utf-8", errors="strict") for item in payload.split(b"\0") if item
            ]
        except UnicodeDecodeError as exc:
            raise WorkspaceIntegrityError("Git returned a non-UTF-8 repository path") from exc
        try:
            paths = _REPOSITORY_PATHS.validate_python(values)
        except ValidationError as exc:
            raise WorkspaceIntegrityError(
                "Git returned a path that is not a portable repository path"
            ) from exc
        if paths != values:
            raise WorkspaceIntegrityError(
                "Git returned a path that is not a portable repository path"
            )
        return paths

    @classmethod
    def _binary_paths(cls, payload: bytes) -> list[RepositoryPath]:
        paths: list[bytes] = []
        for record in payload.split(b"\0"):
            if record.startswith(b"-\t-\t"):
                paths.append(record[4:])
        return cls._decode_paths(b"\0".join(paths))

    @staticmethod
    def _parse_numstat(payload: str) -> tuple[int, int, int]:
        additions = 0
        deletions = 0
        binary = 0
        for line in payload.splitlines():
            fields = line.split("\t", 2)
            if len(fields) < 3:
                raise WorkspaceError("Git returned malformed diff statistics")
            if fields[0] == "-" or fields[1] == "-":
                binary += 1
                continue
            try:
                additions += int(fields[0])
                deletions += int(fields[1])
            except ValueError as exc:
                raise WorkspaceError("Git returned malformed diff statistics") from exc
        return additions, deletions, binary

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise WorkspaceError("Workspace clock must be timezone-aware")
        return value

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True
