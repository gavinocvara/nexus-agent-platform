"""A process sandbox for ephemeral runners that have no Docker daemon.

``LocalProcessSandbox`` implements the PatchForge ``SandboxExecutor`` protocol for the
resident engineer when it runs inside an already-disposable, credential-free environment
such as a GitHub Actions job. It executes only immutable operator-profile commands (no
shell), in a ``.git``-free worktree, with a scrubbed environment that carries no secrets,
bounded by the command's timeout and output limits. It provides none of Docker's kernel
isolation, so it must be enabled explicitly and never used for untrusted repositories.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.sandbox import (
    SandboxError,
    SandboxExecution,
    SandboxPolicyError,
    SandboxRequest,
    SandboxStatus,
)

PYTHON_ALIASES = frozenset({"python", "python3"})
_ALLOWED_HOST_VARIABLES = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "WINDIR")


class LocalSandboxDisabledError(SandboxError):
    """The local process sandbox was requested without explicit enablement."""


class _Capture:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.size = 0
        self.lock = threading.Lock()
        self.exceeded = threading.Event()
        self.chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}

    def append(self, stream: str, chunk: bytes) -> None:
        with self.lock:
            remaining = self.limit - self.size
            if remaining <= 0:
                self.exceeded.set()
                return
            accepted = chunk[:remaining]
            self.chunks[stream].append(accepted)
            self.size += len(accepted)
            if len(accepted) != len(chunk):
                self.exceeded.set()

    def value(self, stream: str) -> bytes:
        return b"".join(self.chunks[stream])


class LocalProcessSandbox:
    """Run operator commands as bounded local processes with a scrubbed environment."""

    def __init__(
        self,
        *,
        allow_local_process: bool,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
        python_executable: str | None = None,
    ) -> None:
        if not allow_local_process:
            raise LocalSandboxDisabledError(
                "The local process sandbox must be enabled explicitly "
                "(NEXUS_SOFTWARE_ENGINEER_SANDBOX=local_process)"
            )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4
        self.python_executable = python_executable or sys.executable
        self.requests: list[SandboxRequest] = []

    def execute(self, request: SandboxRequest) -> SandboxExecution:
        workspace = self._validate_workspace(request.workspace)
        working_directory = self._working_directory(workspace, request.command.working_directory)
        self.requests.append(request)
        started_at = self._now()
        execution_id = self._id_factory()
        limit = min(request.command.max_output_bytes, request.policy.max_output_bytes)
        timeout = min(request.command.timeout_seconds, request.policy.default_timeout_seconds)
        capture = _Capture(limit)
        status = SandboxStatus.SANDBOX_ERROR
        exit_code: int | None = None
        error_code: str | None = "process_start_failed"
        home = tempfile.mkdtemp(prefix="nexus-sandbox-home-")
        try:
            command = self._resolve_command(request.command.executable, request.command.arguments)
            process: subprocess.Popen[bytes] | None
            try:
                process = subprocess.Popen(
                    command,
                    cwd=working_directory,
                    env=self._environment(workspace, home),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    start_new_session=os.name != "nt",
                )
            except OSError:
                process = None
            if process is not None:
                try:
                    status, exit_code, error_code = self._observe(process, capture, timeout)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
        finally:
            shutil.rmtree(home, ignore_errors=True)
        return SandboxExecution(
            execution_id=execution_id,
            run_id=request.run_id,
            call_id=request.call_id,
            status=status,
            exit_code=exit_code,
            stdout=capture.value("stdout"),
            stderr=capture.value("stderr"),
            output_truncated=status is SandboxStatus.OUTPUT_LIMIT,
            command_sha256=canonical_sha256(request.command),
            policy_sha256=canonical_sha256(request.policy),
            started_at=started_at,
            completed_at=max(started_at, self._now()),
            error_code=error_code,
        )

    def _resolve_command(self, executable: str, arguments: Sequence[str]) -> list[str]:
        if executable in PYTHON_ALIASES:
            return [self.python_executable, *arguments]
        resolved = shutil.which(executable)
        if resolved is None:
            raise SandboxError(f"Executable is unavailable: {executable}")
        return [resolved, *arguments]

    @staticmethod
    def _environment(workspace: Path, home: str) -> dict[str, str]:
        environment = {key: os.environ[key] for key in _ALLOWED_HOST_VARIABLES if key in os.environ}
        source = workspace / "src"
        environment.update(
            {
                "HOME": home,
                "TMPDIR": home,
                "TEMP": home,
                "TMP": home,
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": str(source) if source.is_dir() else str(workspace),
                "MYPYPATH": str(source) if source.is_dir() else str(workspace),
            }
        )
        return environment

    @staticmethod
    def _observe(
        process: subprocess.Popen[bytes], capture: _Capture, timeout: int
    ) -> tuple[SandboxStatus, int | None, str | None]:
        if process.stdout is None or process.stderr is None:
            process.kill()
            process.wait()
            return SandboxStatus.SANDBOX_ERROR, None, "pipe_error"
        threads = [
            threading.Thread(
                target=LocalProcessSandbox._drain,
                args=(process.stdout, capture, "stdout"),
                daemon=True,
            ),
            threading.Thread(
                target=LocalProcessSandbox._drain,
                args=(process.stderr, capture, "stderr"),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + timeout
        termination: SandboxStatus | None = None
        while process.poll() is None:
            if capture.exceeded.is_set():
                termination = SandboxStatus.OUTPUT_LIMIT
                break
            if time.monotonic() >= deadline:
                termination = SandboxStatus.TIMED_OUT
                break
            time.sleep(0.01)
        if termination is not None:
            process.kill()
        return_code = process.wait()
        for thread in threads:
            thread.join(timeout=5)
        if capture.exceeded.is_set() and termination is None:
            termination = SandboxStatus.OUTPUT_LIMIT
        if termination is SandboxStatus.OUTPUT_LIMIT:
            return SandboxStatus.OUTPUT_LIMIT, None, "output_limit"
        if termination is SandboxStatus.TIMED_OUT:
            return SandboxStatus.TIMED_OUT, None, "timeout"
        if return_code == 0:
            return SandboxStatus.SUCCEEDED, 0, None
        if return_code < 0 or return_code > 255:
            return SandboxStatus.SANDBOX_ERROR, None, "process_signalled"
        return SandboxStatus.FAILED, return_code, "command_failed"

    @staticmethod
    def _drain(stream: BinaryIO, capture: _Capture, name: str) -> None:
        while chunk := stream.read(65_536):
            capture.append(name, chunk)

    @staticmethod
    def _validate_workspace(workspace: Path) -> Path:
        try:
            resolved = workspace.resolve(strict=True)
        except OSError as exc:
            raise SandboxPolicyError("Sandbox workspace does not exist") from exc
        if not resolved.is_dir() or workspace.is_symlink():
            raise SandboxPolicyError("Sandbox workspace must be a real directory")
        for _, directories, files in os.walk(resolved, followlinks=False):
            if ".git" in directories or ".git" in files:
                raise SandboxPolicyError("Sandbox workspace cannot contain Git metadata")
        return resolved

    @staticmethod
    def _working_directory(workspace: Path, relative: str) -> Path:
        candidate = workspace if relative == "." else workspace.joinpath(*relative.split("/"))
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise SandboxPolicyError("Sandbox working directory does not exist") from exc
        if not resolved.is_relative_to(workspace) or not resolved.is_dir():
            raise SandboxPolicyError("Sandbox working directory is invalid")
        return resolved

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise SandboxError("Sandbox clock must be timezone-aware")
        return value


__all__ = ["PYTHON_ALIASES", "LocalProcessSandbox", "LocalSandboxDisabledError"]
