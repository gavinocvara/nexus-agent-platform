"""LocalProcessSandbox: explicit enablement, scrubbed environment, bounds, no Git."""

import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from nexus.patchforge.policy import SandboxCommand, SandboxPolicy
from nexus.patchforge.sandbox import SandboxError, SandboxPolicyError, SandboxRequest, SandboxStatus
from nexus.software_engineer.sandbox import LocalProcessSandbox, LocalSandboxDisabledError

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)


def _policy(**overrides: int) -> SandboxPolicy:
    values: dict[str, object] = dict(
        image=f"sha256:{'0' * 64}",
        run_as_user="10001:10001",
        cpu_limit_millis=1000,
        memory_limit_mb=256,
        pids_limit=64,
        default_timeout_seconds=30,
        max_output_bytes=100_000,
    )
    values.update(overrides)
    return SandboxPolicy.model_validate(values)


def _request(
    workspace: Path, *arguments: str, timeout: int = 10, output: int = 100_000
) -> SandboxRequest:
    return SandboxRequest(
        run_id=UUID(int=1),
        call_id=UUID(int=2),
        workspace=workspace,
        command=SandboxCommand(
            executable="python",
            arguments=list(arguments),
            timeout_seconds=timeout,
            max_output_bytes=output,
        ),
        policy=_policy(),
    )


def test_local_sandbox_requires_explicit_enablement() -> None:
    with pytest.raises(LocalSandboxDisabledError):
        LocalProcessSandbox(allow_local_process=False)


def test_environment_is_scrubbed_and_python_is_the_runtime_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    monkeypatch.setenv("NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_URL", "https://hooks.example/secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-cross")
    sandbox = LocalProcessSandbox(allow_local_process=True, clock=lambda: NOW)
    execution = sandbox.execute(
        _request(
            workspace,
            "-c",
            "import os, sys; print(sys.executable); print(sorted(k for k in os.environ if "
            "'SECRET' in k or 'KEY' in k or 'SLACK' in k)); print(os.environ['PYTHONPATH']); "
            "print(os.getcwd())",
        )
    )
    assert execution.status is SandboxStatus.SUCCEEDED, execution.stderr
    lines = execution.stdout.decode().splitlines()
    assert lines[0] == sys.executable
    assert lines[1] == "[]"
    assert lines[2] == str((workspace / "src").resolve())
    assert lines[3] == str(workspace.resolve())
    assert execution.completed_at == NOW and execution.error_code is None


def test_bounds_and_failures_are_typed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = LocalProcessSandbox(allow_local_process=True)
    failed = sandbox.execute(_request(workspace, "-c", "import sys; sys.exit(3)"))
    assert failed.status is SandboxStatus.FAILED and failed.exit_code == 3
    timed_out = sandbox.execute(_request(workspace, "-c", "import time; time.sleep(30)", timeout=1))
    assert timed_out.status is SandboxStatus.TIMED_OUT and timed_out.exit_code is None
    limited = sandbox.execute(_request(workspace, "-c", "print('x' * 100000)", output=128))
    assert limited.status is SandboxStatus.OUTPUT_LIMIT and limited.output_truncated
    assert len(limited.stdout) + len(limited.stderr) <= 128
    unknown = _request(workspace, "-c", "0").model_copy(
        update={
            "command": SandboxCommand(
                executable="definitely-not-a-real-binary",
                arguments=[],
                timeout_seconds=5,
                max_output_bytes=100,
            )
        }
    )
    with pytest.raises(SandboxError, match="unavailable"):
        sandbox.execute(unknown)


def test_git_metadata_and_bad_directories_are_refused(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".git").mkdir(parents=True)
    sandbox = LocalProcessSandbox(allow_local_process=True)
    with pytest.raises(SandboxPolicyError, match="Git metadata"):
        sandbox.execute(_request(workspace, "-c", "0"))
    with pytest.raises(SandboxPolicyError, match="does not exist"):
        sandbox.execute(_request(tmp_path / "missing", "-c", "0"))
    clean = tmp_path / "clean"
    clean.mkdir()
    nested = _request(clean, "-c", "0").model_copy(
        update={
            "command": SandboxCommand(
                executable="python",
                arguments=["-c", "0"],
                working_directory="sub",
                timeout_seconds=5,
                max_output_bytes=100,
            )
        }
    )
    with pytest.raises(SandboxPolicyError, match="working directory"):
        sandbox.execute(nested)
