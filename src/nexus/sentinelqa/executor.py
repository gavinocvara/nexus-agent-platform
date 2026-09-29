"""Run operator commands on SentinelQA-built trees and record runtime-attested evidence.

The runner accepts any ``SandboxExecutor`` (the Docker sandbox in production, a scripted
or oracle sandbox in deterministic gates). It fingerprints the tree before and after the
command so a test run that changes the tree is visible, and it takes test counts only
from the runner's own summary line. A runner-integrity probe plants the review's canary
for the duration of one full-suite run and records whether the runner reported it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid5

from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.models import ExecutionStatus
from nexus.patchforge.policy import CommandPurpose, RepositoryProfile
from nexus.patchforge.sandbox import (
    SandboxError,
    SandboxExecutor,
    SandboxRequest,
    SandboxStatus,
)
from nexus.sentinelqa.canary import CanaryError, RunnerCanary, plant_canary, remove_canary
from nexus.sentinelqa.lock import tree_sha256
from nexus.sentinelqa.models import RunnerCanaryRecord, SpecificationRun, SpecificationTree
from nexus.sentinelqa.pytest_summary import parse_pytest_summary

_RUN_NAMESPACE = UUID("2c8a7d1e-5b3f-4e0a-9c6d-7f1e2a3b4c5d")
_STATUS = {
    SandboxStatus.SUCCEEDED: ExecutionStatus.PASSED,
    SandboxStatus.FAILED: ExecutionStatus.FAILED,
    SandboxStatus.TIMED_OUT: ExecutionStatus.TIMED_OUT,
    SandboxStatus.OUTPUT_LIMIT: ExecutionStatus.ERROR,
    SandboxStatus.SANDBOX_ERROR: ExecutionStatus.ERROR,
}


class SpecificationExecutionError(RuntimeError):
    """The sandbox could not execute a specification command at all."""


class SpecificationRunner:
    """Execute operator-profile commands for one review and attest each run."""

    def __init__(
        self,
        *,
        review_id: UUID,
        profile: RepositoryProfile,
        sandbox: SandboxExecutor,
        clock: Callable[[], datetime],
    ) -> None:
        self.review_id = review_id
        self.profile = profile
        self.sandbox = sandbox
        self.clock = clock
        self.runs: list[SpecificationRun] = []

    def run(
        self,
        tree: SpecificationTree,
        root: Path,
        purpose: CommandPurpose,
        *,
        canary: RunnerCanary | None = None,
    ) -> SpecificationRun:
        command = self.profile.commands.get(purpose)
        if command is None:
            raise SpecificationExecutionError(f"Profile defines no {purpose} command")
        if canary is not None and purpose is not CommandPurpose.FULL_TEST_SUITE:
            raise SpecificationExecutionError("Only the full-suite command carries a canary")
        sequence = len(self.runs) + 1
        request = SandboxRequest(
            run_id=self.review_id,
            call_id=uuid5(_RUN_NAMESPACE, f"{self.review_id}:{sequence}"),
            workspace=root,
            command=command,
            policy=self.profile.sandbox,
        )
        if canary is not None:
            try:
                plant_canary(root, canary)
            except (CanaryError, OSError) as exc:
                raise SpecificationExecutionError("The canary could not be planted") from exc
        try:
            before = tree_sha256(root)
            started_at = self._now()
            try:
                execution = self.sandbox.execute(request)
            except SandboxError as exc:
                raise SpecificationExecutionError(str(exc)) from exc
            after = tree_sha256(root)
        finally:
            if canary is not None:
                remove_canary(root, canary)
        status = _STATUS[execution.status]
        conclusive = status in {ExecutionStatus.PASSED, ExecutionStatus.FAILED}
        record = SpecificationRun(
            review_id=self.review_id,
            sequence=sequence,
            tree=tree,
            purpose=purpose,
            command_sha256=canonical_sha256(command),
            policy_sha256=canonical_sha256(self.profile.sandbox),
            status=status,
            exit_code=execution.exit_code if conclusive else None,
            counts=parse_pytest_summary(execution.stdout) if conclusive else None,
            tree_sha256_before=before,
            tree_sha256_after=after,
            stdout_sha256=sha256(execution.stdout).hexdigest(),
            stderr_sha256=sha256(execution.stderr).hexdigest(),
            output_truncated=execution.output_truncated,
            started_at=started_at,
            completed_at=max(started_at, self._now(), execution.completed_at),
            canary=(
                RunnerCanaryRecord(
                    path=canary.path,
                    sha256=canary.sha256,
                    reported_failed=conclusive and canary.reported_failed(execution.stdout),
                )
                if canary is not None
                else None
            ),
        )
        self.runs.append(record)
        return record

    def _now(self) -> datetime:
        value = self.clock()
        if value.utcoffset() is None:
            raise SpecificationExecutionError("SentinelQA clock must be timezone-aware")
        return value


__all__ = ["SpecificationExecutionError", "SpecificationRunner"]
