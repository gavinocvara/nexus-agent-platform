"""Evaluator-only clean-stack and deterministic warm-up controls."""

import subprocess
from time import monotonic, sleep
from typing import Protocol

import httpx

from nexus.diagnostics.config import DiagnosticsSettings


class EnvironmentIntegrityError(RuntimeError):
    """The benchmark lab could not be reset to a known healthy state."""


class StackController(Protocol):
    def clean_restart(self) -> None: ...


class DockerComposeStackController:
    """Reset applications, counters, telemetry, and volumes with fixed Compose commands."""

    def __init__(self, timeout_seconds: float = 180) -> None:
        self.timeout_seconds = timeout_seconds

    def clean_restart(self) -> None:
        self._run(["docker", "compose", "down", "--volumes", "--remove-orphans"])
        self._run(["docker", "compose", "up", "--build", "--detach", "--wait"])

    def _run(self, command: list[str]) -> None:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise EnvironmentIntegrityError(
                f"Stack command timed out after {self.timeout_seconds:g} seconds: "
                f"{' '.join(command)}"
            ) from exc
        except OSError as exc:
            raise EnvironmentIntegrityError(
                f"Stack command could not start ({type(exc).__name__}): {' '.join(command)}"
            ) from exc
        if completed.returncode != 0:
            raise EnvironmentIntegrityError(
                f"Stack command exited with code {completed.returncode}: {' '.join(command)}"
            )


class BenchmarkWarmup:
    """Generate only healthy, non-scenario traffic and wait for two scrape intervals."""

    def __init__(
        self,
        settings: DiagnosticsSettings | None = None,
        warmup_seconds: float = 6,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or DiagnosticsSettings()
        self.warmup_seconds = warmup_seconds
        self.transport = transport

    def run(self) -> None:
        with httpx.Client(timeout=10, transport=self.transport) as client:
            endpoints = (
                f"{str(self.settings.gateway_url).rstrip('/')}/health",
                f"{str(self.settings.users_url).rstrip('/')}/health",
                f"{str(self.settings.orders_url).rstrip('/')}/health",
                f"{str(self.settings.prometheus_url).rstrip('/')}/-/ready",
                f"{str(self.settings.loki_url).rstrip('/')}/ready",
                f"{str(self.settings.tempo_url).rstrip('/')}/ready",
            )
            for endpoint in endpoints:
                self._wait_for_200(client, endpoint)
            response = client.get(
                f"{str(self.settings.gateway_url).rstrip('/')}/users/1",
                headers={"X-Correlation-ID": "benchmark-warmup-users"},
            )
            if response.status_code != 200:
                raise EnvironmentIntegrityError("Healthy users warm-up request failed")
            response = client.get(
                f"{str(self.settings.gateway_url).rstrip('/')}/orders/"
                "00000000-0000-0000-0000-000000000000",
                headers={"X-Correlation-ID": "benchmark-warmup-orders"},
            )
            if response.status_code != 404:
                raise EnvironmentIntegrityError("Healthy orders warm-up request was unexpected")
        sleep(self.warmup_seconds)

    @staticmethod
    def _wait_for_200(client: httpx.Client, endpoint: str) -> None:
        deadline = monotonic() + 30
        while monotonic() < deadline:
            try:
                if client.get(endpoint).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            sleep(0.5)
        raise EnvironmentIntegrityError(f"Warm-up endpoint unavailable: {endpoint}")
