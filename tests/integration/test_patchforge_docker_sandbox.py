"""Real Docker isolation proof for the PatchForge sandbox."""

import json
import os
import subprocess
from pathlib import Path
from uuid import UUID

import pytest

from nexus.patchforge.policy import SandboxCommand, SandboxPolicy
from nexus.patchforge.sandbox import DockerSandbox, SandboxRequest, SandboxStatus

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def require_compose() -> None:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 with the Compose environment running")


def _local_image_id() -> str:
    result = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            "nexus-aegisops-users:latest",
            "--format",
            "{{.Id}}",
        ],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        shell=False,
    )
    return result.stdout.decode("utf-8").strip()


def _assert_container_absent(name: str) -> None:
    result = subprocess.run(
        ["docker", "ps", "--all", "--quiet", "--filter", f"name=^{name}$"],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        shell=False,
    )
    assert result.stdout.strip() == b""


def test_real_docker_sandbox_enforces_runtime_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    os.chmod(workspace, 0o777)
    (workspace / "input.txt").write_bytes(b"sandbox input\n")
    os.chmod(workspace / "input.txt", 0o666)
    monkeypatch.setenv("PATCHFORGE_HOST_SECRET", "must-not-cross-boundary")
    script = """
import json
import os
import socket
from pathlib import Path

network_blocked = False
try:
    socket.create_connection(("1.1.1.1", 53), timeout=0.2)
except OSError:
    network_blocked = True

root_read_only = False
try:
    Path("/patchforge-root-write").write_text("forbidden")
except OSError:
    root_read_only = True

Path("result.txt").write_text(Path("input.txt").read_text().upper())
print(json.dumps({
    "git_present": Path(".git").exists(),
    "network_blocked": network_blocked,
    "root_read_only": root_read_only,
    "secret_present": "PATCHFORGE_HOST_SECRET" in os.environ,
    "uid": os.getuid(),
}))
""".strip()
    (workspace / "probe.py").write_text(script, encoding="utf-8", newline="\n")
    os.chmod(workspace / "probe.py", 0o644)
    policy = SandboxPolicy(
        image=_local_image_id(),
        run_as_user="10001:10001",
        cpu_limit_millis=500,
        memory_limit_mb=256,
        pids_limit=32,
        default_timeout_seconds=20,
        max_output_bytes=10_000,
    )
    request = SandboxRequest(
        run_id=UUID(int=101),
        call_id=UUID(int=102),
        workspace=workspace,
        command=SandboxCommand(
            executable="python",
            arguments=["probe.py"],
            timeout_seconds=10,
            max_output_bytes=10_000,
        ),
        policy=policy,
    )

    execution = DockerSandbox().execute(request)

    assert execution.status is SandboxStatus.SUCCEEDED, execution.stderr.decode(
        "utf-8", errors="replace"
    )
    evidence = json.loads(execution.stdout)
    assert evidence == {
        "git_present": False,
        "network_blocked": True,
        "root_read_only": True,
        "secret_present": False,
        "uid": 10001,
    }
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "SANDBOX INPUT\n"
    _assert_container_absent(DockerSandbox.container_name(request))


def test_real_docker_sandbox_terminates_on_output_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = SandboxPolicy(
        image=_local_image_id(),
        run_as_user="10001:10001",
        cpu_limit_millis=500,
        memory_limit_mb=256,
        pids_limit=32,
        default_timeout_seconds=10,
        max_output_bytes=128,
    )
    request = SandboxRequest(
        run_id=UUID(int=103),
        call_id=UUID(int=104),
        workspace=workspace,
        command=SandboxCommand(
            executable="python",
            arguments=["-c", "print('x' * 100000)"],
            timeout_seconds=10,
            max_output_bytes=128,
        ),
        policy=policy,
    )

    execution = DockerSandbox().execute(request)

    assert execution.status is SandboxStatus.OUTPUT_LIMIT
    assert execution.output_truncated is True
    assert len(execution.stdout) + len(execution.stderr) <= 128
    _assert_container_absent(DockerSandbox.container_name(request))


def test_real_docker_sandbox_terminates_on_timeout(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = SandboxPolicy(
        image=_local_image_id(),
        run_as_user="10001:10001",
        cpu_limit_millis=500,
        memory_limit_mb=256,
        pids_limit=32,
        default_timeout_seconds=1,
        max_output_bytes=1000,
    )
    request = SandboxRequest(
        run_id=UUID(int=105),
        call_id=UUID(int=106),
        workspace=workspace,
        command=SandboxCommand(
            executable="python",
            arguments=["-c", "import time; time.sleep(30)"],
            timeout_seconds=1,
            max_output_bytes=1000,
        ),
        policy=policy,
    )

    execution = DockerSandbox().execute(request)

    assert execution.status is SandboxStatus.TIMED_OUT
    assert execution.exit_code is None
    _assert_container_absent(DockerSandbox.container_name(request))
