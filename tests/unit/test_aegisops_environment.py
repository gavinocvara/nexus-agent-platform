"""Regression coverage for evaluator-owned environment controls."""

import subprocess
from unittest.mock import patch

import pytest

from nexus.evaluation.aegisops.environment import (
    DockerComposeStackController,
    EnvironmentIntegrityError,
)

COMMAND = ["docker", "compose", "up", "--detach", "--wait"]


@pytest.mark.parametrize(
    "stdout, stderr",
    [
        (b"\x81", b""),
        ("Compose ready: \u2713".encode(), b""),
        (b"\x00\x81\xfe\xff", b"\xff\x81"),
    ],
)
def test_success_does_not_depend_on_subprocess_output_encoding(
    stdout: bytes, stderr: bytes
) -> None:
    completed = subprocess.CompletedProcess(COMMAND, 0, stdout=stdout, stderr=stderr)

    with patch(
        "nexus.evaluation.aegisops.environment.subprocess.run", return_value=completed
    ) as run:
        DockerComposeStackController(timeout_seconds=23)._run(COMMAND)

    run.assert_called_once_with(
        COMMAND,
        check=False,
        capture_output=True,
        text=False,
        timeout=23,
    )


def test_nonzero_exit_with_arbitrary_bytes_raises_safe_error() -> None:
    completed = subprocess.CompletedProcess(
        COMMAND,
        17,
        stdout=b"\x81arbitrary output",
        stderr=b"secret-looking=\xff\xfe\x81",
    )

    with (
        patch("nexus.evaluation.aegisops.environment.subprocess.run", return_value=completed),
        pytest.raises(EnvironmentIntegrityError) as raised,
    ):
        DockerComposeStackController()._run(COMMAND)

    assert str(raised.value) == (
        "Stack command exited with code 17: docker compose up --detach --wait"
    )
    assert "arbitrary output" not in str(raised.value)
    assert "secret-looking" not in str(raised.value)


def test_timeout_is_classified_without_decoding_captured_output() -> None:
    timeout = subprocess.TimeoutExpired(
        COMMAND,
        7,
        output=b"\x81partial output",
        stderr=b"\xffpartial error",
    )

    with (
        patch("nexus.evaluation.aegisops.environment.subprocess.run", side_effect=timeout),
        pytest.raises(EnvironmentIntegrityError) as raised,
    ):
        DockerComposeStackController(timeout_seconds=7)._run(COMMAND)

    assert str(raised.value) == (
        "Stack command timed out after 7 seconds: docker compose up --detach --wait"
    )
    assert raised.value.__cause__ is timeout


def test_os_failure_is_classified_without_exposing_exception_details() -> None:
    failure = FileNotFoundError("sensitive local path")

    with (
        patch("nexus.evaluation.aegisops.environment.subprocess.run", side_effect=failure),
        pytest.raises(EnvironmentIntegrityError) as raised,
    ):
        DockerComposeStackController()._run(COMMAND)

    assert str(raised.value) == (
        "Stack command could not start (FileNotFoundError): docker compose up --detach --wait"
    )
    assert "sensitive local path" not in str(raised.value)
    assert raised.value.__cause__ is failure
