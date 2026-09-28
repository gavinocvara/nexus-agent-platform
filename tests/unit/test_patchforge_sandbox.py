"""PatchForge fake and Docker sandbox boundary tests."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.policy import SandboxCommand, SandboxPolicy
from nexus.patchforge.sandbox import (
    DockerSandbox,
    FakeSandbox,
    FakeSandboxPlan,
    SandboxError,
    SandboxPolicyError,
    SandboxRequest,
    SandboxStatus,
)

NOW = datetime(2026, 9, 28, 3, tzinfo=UTC)


def _policy(*, output_limit: int = 1000) -> SandboxPolicy:
    return SandboxPolicy(
        image=f"sha256:{'a' * 64}",
        run_as_user="10001:10001",
        cpu_limit_millis=750,
        memory_limit_mb=256,
        pids_limit=64,
        default_timeout_seconds=30,
        max_output_bytes=output_limit,
    )


def _command(*, output_limit: int = 1000) -> SandboxCommand:
    return SandboxCommand(
        executable="python",
        arguments=["-m", "pytest"],
        timeout_seconds=20,
        max_output_bytes=output_limit,
    )


def _request(workspace: Path, *, output_limit: int = 1000) -> SandboxRequest:
    return SandboxRequest(
        run_id=UUID(int=1),
        call_id=UUID(int=2),
        workspace=workspace,
        command=_command(output_limit=output_limit),
        policy=_policy(output_limit=output_limit),
    )


def test_fake_sandbox_returns_runtime_attested_deterministic_execution(tmp_path: Path) -> None:
    command = _command()
    fake = FakeSandbox(
        [
            FakeSandboxPlan(
                expected_command_sha256=canonical_sha256(command),
                status=SandboxStatus.SUCCEEDED,
                exit_code=0,
                stdout=b"2 passed\n",
                duration_seconds=1.5,
            )
        ],
        clock=lambda: NOW,
        id_factory=lambda: UUID(int=3),
    )
    request = _request(tmp_path)
    execution = fake.execute(request)
    assert execution.execution_id == UUID(int=3)
    assert execution.status is SandboxStatus.SUCCEEDED
    assert execution.stdout == b"2 passed\n"
    assert execution.command_sha256 == canonical_sha256(command)
    assert execution.policy_sha256 == canonical_sha256(request.policy)
    assert (execution.completed_at - execution.started_at).total_seconds() == 1.5
    assert fake.requests == [request]


def test_fake_sandbox_rejects_an_unplanned_command(tmp_path: Path) -> None:
    fake = FakeSandbox(
        [
            FakeSandboxPlan(
                expected_command_sha256="f" * 64,
                status=SandboxStatus.SUCCEEDED,
                exit_code=0,
            )
        ]
    )
    with pytest.raises(SandboxError, match="does not match"):
        fake.execute(_request(tmp_path))


def test_fake_sandbox_enforces_combined_output_limit(tmp_path: Path) -> None:
    request = _request(tmp_path, output_limit=5)
    fake = FakeSandbox(
        [
            FakeSandboxPlan(
                expected_command_sha256=canonical_sha256(request.command),
                status=SandboxStatus.SUCCEEDED,
                exit_code=0,
                stdout=b"abcdef",
                stderr=b"ghijkl",
            )
        ],
        clock=lambda: NOW,
        id_factory=lambda: UUID(int=4),
    )
    execution = fake.execute(request)
    assert execution.status is SandboxStatus.OUTPUT_LIMIT
    assert execution.exit_code is None
    assert execution.output_truncated is True
    assert execution.stdout == b"abcde"
    assert execution.stderr == b""
    assert execution.error_code == "output_limit"


def test_fake_sandbox_requires_a_complete_plan(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="no remaining"):
        FakeSandbox([]).execute(_request(tmp_path))


def test_fake_sandbox_plan_rejects_fabricated_output_limit() -> None:
    with pytest.raises(ValidationError, match="must arise"):
        FakeSandboxPlan(
            expected_command_sha256="f" * 64,
            status=SandboxStatus.OUTPUT_LIMIT,
            error_code="output_limit",
        )


def test_docker_command_enforces_every_v1_isolation_flag(tmp_path: Path) -> None:
    docker = DockerSandbox(docker_executable=str(tmp_path / "docker"))
    request = _request(tmp_path)
    command = docker.build_command(request, tmp_path.resolve(), docker.container_name(request))
    joined = " ".join(command)
    assert "--pull=never" in command
    assert "--network=none" in command
    assert "--user 10001:10001" in joined
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--cpus 0.750" in joined
    assert "--memory 256m" in joined
    assert "--memory-swap 256m" in joined
    assert "--pids-limit 64" in joined
    assert "/tmp:rw,noexec,nosuid,nodev,size=64m" in command
    assert f"type=bind,source={tmp_path.resolve()},target=/workspace" in command
    assert "--workdir /workspace" in joined
    assert "--entrypoint python" in joined
    assert request.policy.image in command
    assert "--env-file" not in command
    assert "--privileged" not in command
    assert "--volume" not in command


def test_docker_command_uses_only_a_verified_repository_working_directory(
    tmp_path: Path,
) -> None:
    subdirectory = tmp_path / "tests"
    subdirectory.mkdir()
    request = _request(tmp_path).model_copy(
        update={
            "command": _command().model_copy(update={"working_directory": "tests"}),
        }
    )
    docker = DockerSandbox(docker_executable=str(tmp_path / "docker"))

    docker._validate_working_directory(tmp_path.resolve(), "tests")
    command = docker.build_command(request, tmp_path.resolve(), docker.container_name(request))
    assert "/workspace/tests" in command

    with pytest.raises(SandboxPolicyError, match="does not exist"):
        docker._validate_working_directory(tmp_path.resolve(), "missing")


@pytest.mark.parametrize("entry_type", ["file", "directory"])
def test_docker_sandbox_rejects_git_metadata_anywhere(
    tmp_path: Path,
    entry_type: str,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    git_entry = nested / ".git"
    if entry_type == "file":
        git_entry.write_text("gitdir: elsewhere", encoding="utf-8")
    else:
        git_entry.mkdir()
    with pytest.raises(SandboxPolicyError, match="Git metadata"):
        DockerSandbox._validate_workspace(tmp_path)


def test_docker_sandbox_always_attempts_cleanup_after_observation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedProcess:
        terminated = False

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            self.terminated = True

        def wait(self) -> int:
            return -1

    docker = DockerSandbox(docker_executable=str(tmp_path / "docker"))
    request = _request(tmp_path)
    process = UnexpectedProcess()
    removed: list[str] = []
    monkeypatch.setattr(
        "nexus.patchforge.sandbox.subprocess.Popen", lambda *args, **kwargs: process
    )
    monkeypatch.setattr(
        docker,
        "_observe_process",
        lambda *args: (_ for _ in ()).throw(RuntimeError("observation failed")),
    )
    monkeypatch.setattr(docker, "_remove_container", removed.append)

    with pytest.raises(RuntimeError, match="observation failed"):
        docker.execute(request)

    assert process.terminated is True
    assert removed == [docker.container_name(request)]


def test_sandbox_policy_accepts_only_content_addressed_images() -> None:
    assert _policy().image == f"sha256:{'a' * 64}"
    with pytest.raises(ValidationError):
        SandboxPolicy(
            image="python:latest",
            run_as_user="10001:10001",
            cpu_limit_millis=1000,
            memory_limit_mb=256,
            pids_limit=64,
            default_timeout_seconds=30,
            max_output_bytes=1000,
        )
