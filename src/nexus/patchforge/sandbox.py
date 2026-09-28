"""Networkless Docker sandbox and deterministic fake for PatchForge execution."""

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Literal, Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from nexus.atlas.models import Identifier, Sha256, StrictModel
from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.policy import SandboxCommand, SandboxPolicy

Clock = Callable[[], datetime]
IdFactory = Callable[[], UUID]


class SandboxError(RuntimeError):
    """Sandbox execution could not be performed safely."""


class SandboxPolicyError(SandboxError):
    """A request violates a non-negotiable sandbox boundary."""


class SandboxStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    OUTPUT_LIMIT = "output_limit"
    SANDBOX_ERROR = "sandbox_error"


class SandboxRequest(StrictModel):
    schema_version: Literal[1] = 1
    run_id: UUID
    call_id: UUID
    workspace: Path
    command: SandboxCommand
    policy: SandboxPolicy


class SandboxExecution(StrictModel):
    schema_version: Literal[1] = 1
    execution_id: UUID
    run_id: UUID
    call_id: UUID
    status: SandboxStatus
    exit_code: int | None = Field(default=None, ge=0, le=255)
    stdout: bytes
    stderr: bytes
    output_truncated: bool
    command_sha256: Sha256
    policy_sha256: Sha256
    started_at: AwareDatetime
    completed_at: AwareDatetime
    error_code: Identifier | None = None
    attested_by: Literal["patchforge.runtime"] = "patchforge.runtime"

    @model_validator(mode="after")
    def validate_execution(self) -> "SandboxExecution":
        if self.completed_at < self.started_at:
            raise ValueError("Sandbox completion cannot precede start")
        if self.status is SandboxStatus.SUCCEEDED:
            if self.exit_code != 0 or self.error_code is not None or self.output_truncated:
                raise ValueError("Successful sandbox execution has inconsistent evidence")
        elif self.status is SandboxStatus.FAILED:
            if self.exit_code is None or self.exit_code == 0 or self.error_code is None:
                raise ValueError("Failed sandbox execution requires exit and error evidence")
        else:
            if self.exit_code is not None or self.error_code is None:
                raise ValueError("Exceptional sandbox execution has inconsistent evidence")
        if self.status is SandboxStatus.OUTPUT_LIMIT and not self.output_truncated:
            raise ValueError("Output-limit status must record truncation")
        return self


@runtime_checkable
class SandboxExecutor(Protocol):
    def execute(self, request: SandboxRequest) -> SandboxExecution: ...


class FakeSandboxPlan(StrictModel):
    expected_command_sha256: Sha256
    status: SandboxStatus
    exit_code: int | None = Field(default=None, ge=0, le=255)
    stdout: bytes = b""
    stderr: bytes = b""
    duration_seconds: float = Field(default=0.0, ge=0, le=86_400)
    error_code: Identifier | None = None

    @model_validator(mode="after")
    def validate_plan(self) -> "FakeSandboxPlan":
        if self.status is SandboxStatus.OUTPUT_LIMIT:
            raise ValueError("Fake output-limit status must arise from bounded output")
        if self.status is SandboxStatus.SUCCEEDED:
            if self.exit_code != 0 or self.error_code is not None:
                raise ValueError("Successful fake plan requires exit zero and no error")
        elif self.status is SandboxStatus.FAILED:
            if self.exit_code is None or self.exit_code == 0 or self.error_code is None:
                raise ValueError("Failed fake plan requires nonzero exit and an error")
        elif self.exit_code is not None or self.error_code is None:
            raise ValueError("Exceptional fake plan requires only an error code")
        return self


class FakeSandbox:
    """Scripted sandbox that applies the same evidence and output contracts without I/O."""

    def __init__(
        self,
        plans: Iterable[FakeSandboxPlan],
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._plans = list(plans)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4
        self.requests: list[SandboxRequest] = []

    def execute(self, request: SandboxRequest) -> SandboxExecution:
        if not self._plans:
            raise SandboxError("Fake sandbox has no remaining execution plan")
        plan = self._plans.pop(0)
        command_hash = canonical_sha256(request.command)
        if command_hash != plan.expected_command_sha256:
            raise SandboxError("Fake sandbox command does not match its plan")
        self.requests.append(request)
        started_at = self._now()
        limit = min(request.command.max_output_bytes, request.policy.max_output_bytes)
        stdout, stderr, truncated = _bounded_outputs(plan.stdout, plan.stderr, limit)
        status = SandboxStatus.OUTPUT_LIMIT if truncated else plan.status
        exit_code = None if truncated else plan.exit_code
        error_code = "output_limit" if truncated else plan.error_code
        completed_at = started_at + timedelta(seconds=plan.duration_seconds)
        return SandboxExecution(
            execution_id=self._id_factory(),
            run_id=request.run_id,
            call_id=request.call_id,
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            output_truncated=truncated,
            command_sha256=command_hash,
            policy_sha256=canonical_sha256(request.policy),
            started_at=started_at,
            completed_at=completed_at,
            error_code=error_code,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise SandboxError("Sandbox clock must be timezone-aware")
        return value


class _ProcessCapture:
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


class DockerSandbox:
    """Run one operator-owned command in a disposable, resource-bounded container."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        docker_executable: str | None = None,
    ) -> None:
        executable = docker_executable or shutil.which("docker")
        if executable is None:
            raise SandboxError("Docker executable is unavailable")
        self.docker_executable = str(Path(executable).resolve())
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4

    def execute(self, request: SandboxRequest) -> SandboxExecution:
        workspace = self._validate_workspace(request.workspace)
        self._validate_working_directory(workspace, request.command.working_directory)
        started_at = self._now()
        execution_id = self._id_factory()
        container_name = self.container_name(request)
        command = self.build_command(request, workspace, container_name)
        limit = min(request.command.max_output_bytes, request.policy.max_output_bytes)
        timeout = min(
            request.command.timeout_seconds,
            request.policy.default_timeout_seconds,
        )
        capture = _ProcessCapture(limit)
        status = SandboxStatus.SANDBOX_ERROR
        exit_code: int | None = None
        error_code: str | None = "docker_start_failed"
        try:
            process = subprocess.Popen(
                command,
                env=self._environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=os.name != "nt",
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
                ),
            )
        except OSError:
            process = None
        try:
            if process is not None:
                status, exit_code, error_code = self._observe_process(
                    process,
                    capture,
                    timeout,
                )
        finally:
            try:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()
            finally:
                self._remove_container(container_name)
        completed_at = self._now()
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
            completed_at=completed_at,
            error_code=error_code,
        )

    def _observe_process(
        self,
        process: subprocess.Popen[bytes],
        capture: _ProcessCapture,
        timeout: int,
    ) -> tuple[SandboxStatus, int | None, str | None]:
        if process.stdout is None or process.stderr is None:
            process.kill()
            process.wait()
            return SandboxStatus.SANDBOX_ERROR, None, "docker_pipe_error"
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
        if return_code < 0 or return_code in {125, 126, 127}:
            return SandboxStatus.SANDBOX_ERROR, None, "docker_runtime_error"
        return SandboxStatus.FAILED, return_code, "command_failed"

    def build_command(
        self,
        request: SandboxRequest,
        workspace: Path,
        container_name: str,
    ) -> list[str]:
        policy = request.policy
        cpu_limit = f"{policy.cpu_limit_millis / 1000:.3f}"
        mount = f"type=bind,source={workspace},target=/workspace"
        container_workdir = "/workspace"
        if request.command.working_directory != ".":
            container_workdir = f"/workspace/{request.command.working_directory}"
        return [
            self.docker_executable,
            "run",
            "--rm",
            "--pull=never",
            "--name",
            container_name,
            "--label",
            "nexus.component=patchforge",
            "--label",
            f"nexus.patchforge.run_id={request.run_id}",
            "--network=none",
            "--user",
            policy.run_as_user,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--cpus",
            cpu_limit,
            "--memory",
            f"{policy.memory_limit_mb}m",
            "--memory-swap",
            f"{policy.memory_limit_mb}m",
            "--pids-limit",
            str(policy.pids_limit),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--mount",
            mount,
            "--workdir",
            container_workdir,
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--entrypoint",
            request.command.executable,
            policy.image,
            *request.command.arguments,
        ]

    @staticmethod
    def container_name(request: SandboxRequest) -> str:
        return f"nexus-patchforge-{request.run_id.hex[:12]}-{request.call_id.hex[:12]}"

    @staticmethod
    def _validate_workspace(workspace: Path) -> Path:
        try:
            resolved = workspace.resolve(strict=True)
        except OSError as exc:
            raise SandboxPolicyError("Sandbox workspace does not exist") from exc
        if not resolved.is_dir() or workspace.is_symlink():
            raise SandboxPolicyError("Sandbox workspace must be a real directory")
        if "," in str(resolved):
            raise SandboxPolicyError("Sandbox workspace path cannot contain a comma")
        for _, directories, files in os.walk(resolved, followlinks=False):
            if ".git" in directories or ".git" in files:
                raise SandboxPolicyError("Sandbox workspace cannot contain Git metadata")
        return resolved

    @staticmethod
    def _validate_working_directory(workspace: Path, relative: str) -> None:
        candidate = workspace if relative == "." else workspace.joinpath(*relative.split("/"))
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise SandboxPolicyError("Sandbox working directory does not exist") from exc
        if not resolved.is_relative_to(workspace) or not resolved.is_dir():
            raise SandboxPolicyError("Sandbox working directory is invalid")
        current = workspace
        for part in () if relative == "." else relative.split("/"):
            current = current / part
            if current.is_symlink():
                raise SandboxPolicyError("Sandbox working directory cannot use symbolic links")

    @staticmethod
    def _drain(stream: BinaryIO, capture: _ProcessCapture, name: str) -> None:
        while chunk := stream.read(65_536):
            capture.append(name, chunk)

    def _remove_container(self, container_name: str) -> None:
        try:
            subprocess.run(
                [self.docker_executable, "rm", "--force", container_name],
                env=self._environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return

    @staticmethod
    def _environment() -> dict[str, str]:
        allowed = (
            "COMSPEC",
            "DOCKER_CONTEXT",
            "DOCKER_HOST",
            "PATH",
            "PATHEXT",
            "SYSTEMDRIVE",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "WINDIR",
        )
        return {key: os.environ[key] for key in allowed if key in os.environ}

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise SandboxError("Sandbox clock must be timezone-aware")
        return value


def _bounded_outputs(stdout: bytes, stderr: bytes, limit: int) -> tuple[bytes, bytes, bool]:
    combined = len(stdout) + len(stderr)
    if combined <= limit:
        return stdout, stderr, False
    stdout_part = stdout[:limit]
    remaining = limit - len(stdout_part)
    return stdout_part, stderr[:remaining], True
