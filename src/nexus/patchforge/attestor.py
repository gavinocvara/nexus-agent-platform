"""Runtime-owned attestation that turns a closed PatchForge run into a PatchResult."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid5

from nexus.atlas.models import ArtifactReference, Identifier
from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.gateway import (
    EXECUTION_TOOLS,
    command_purpose_for,
    is_sensitive_path,
    is_test_path,
    path_in_scope,
    path_matches,
)
from nexus.patchforge.models import (
    AgentReport,
    CheckKind,
    CheckResult,
    CheckStatus,
    DiffSummary,
    EngineeringTask,
    ExecutionStatus,
    FindingSeverity,
    PatchForgeFailure,
    PatchForgePhase,
    PatchOutcome,
    PatchResult,
    PolicyFinding,
    ReproductionEvidence,
    ReproductionStatus,
    TestExecution,
    ToolCallRecord,
)
from nexus.patchforge.policy import CommandPurpose, PatchForgePolicy, RepositoryProfile
from nexus.patchforge.runtime import (
    STATE_TRACKED_TOOLS,
    FinalCaptureError,
    RuntimeCompletion,
    RuntimeTransitionKind,
    WorkspaceStateKind,
    WorkspaceStateRecord,
    outcome_for_failure,
)
from nexus.patchforge.sandbox import SandboxExecution, SandboxStatus

PATCH_ARTIFACT_TYPE = "patchforge_patch"
_FINDING_NAMESPACE = UUID("5f0e8a52-4a3f-4b0e-9d2f-6b1f3c1a7e10")
_REQUIRED_CHECKS = (CheckKind.TARGETED_TESTS, CheckKind.FULL_TEST_SUITE)
_OPTIONAL_CHECKS = (CheckKind.FORMATTER, CheckKind.LINTER, CheckKind.TYPECHECK)
_EXECUTION_STATUS = {
    SandboxStatus.SUCCEEDED: ExecutionStatus.PASSED,
    SandboxStatus.FAILED: ExecutionStatus.FAILED,
    SandboxStatus.TIMED_OUT: ExecutionStatus.TIMED_OUT,
    SandboxStatus.OUTPUT_LIMIT: ExecutionStatus.ERROR,
    SandboxStatus.SANDBOX_ERROR: ExecutionStatus.ERROR,
}
_CHECK_STATUS = {
    ExecutionStatus.PASSED: CheckStatus.PASSED,
    ExecutionStatus.FAILED: CheckStatus.FAILED,
    ExecutionStatus.ERROR: CheckStatus.ERROR,
    ExecutionStatus.TIMED_OUT: CheckStatus.ERROR,
}


class ArtifactStoreError(RuntimeError):
    """An attestation artifact cannot be stored or verified."""


class ArtifactStore(Protocol):
    def put(self, content: bytes, *, artifact_type: Identifier) -> ArtifactReference: ...


class LocalArtifactStore:
    """Content-addressed local store under an operator-chosen (ignored) directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes, *, artifact_type: Identifier) -> ArtifactReference:
        digest = sha256(content).hexdigest()
        target = self.root / f"{digest}.{artifact_type}"
        if target.exists():
            if sha256(target.read_bytes()).hexdigest() != digest:
                raise ArtifactStoreError("Stored artifact does not match its content address")
        else:
            temporary = self.root / f".{digest}.{os.getpid()}.tmp"
            try:
                temporary.write_bytes(content)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return ArtifactReference(
            artifact_type=artifact_type,
            uri=f"nexus-artifact:sha256:{digest}",
            sha256=digest,
        )

    def read(self, reference: ArtifactReference) -> bytes:
        target = self.root / f"{reference.sha256}.{reference.artifact_type}"
        content = target.read_bytes()
        if sha256(content).hexdigest() != reference.sha256:
            raise ArtifactStoreError("Stored artifact does not match its content address")
        return content


class FindingCategory(StrEnum):
    """Which failure a blocking finding implies; tampering outranks policy and validation."""

    TAMPER = "tamper"
    POLICY = "policy"
    VALIDATION = "validation"


_CATEGORY_FAILURE = {
    FindingCategory.TAMPER: PatchForgeFailure.ATTESTATION_FAILED,
    FindingCategory.POLICY: PatchForgeFailure.POLICY_DENIED,
    FindingCategory.VALIDATION: PatchForgeFailure.VALIDATION_FAILED,
}


@dataclass(frozen=True, slots=True)
class _Finding:
    code: str
    severity: FindingSeverity
    category: FindingCategory
    detail: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class _Execution:
    record: ToolCallRecord
    sandbox: SandboxExecution
    purpose: CommandPurpose
    evidence: TestExecution


class PatchForgeAttestor:
    """Attest a closed Runtime run from runtime-owned evidence only.

    The attestor never executes commands or trusts model narrative. Validation counts only
    when its execution observed the exact final tree and did not change it.
    """

    def __init__(
        self,
        *,
        task: EngineeringTask,
        profile: RepositoryProfile,
        policy: PatchForgePolicy,
        artifact_store: ArtifactStore,
    ) -> None:
        if canonical_sha256(profile) != task.repository_profile_sha256:
            raise ValueError("Attestor profile does not match the task binding")
        if policy.repository_profile_sha256 != task.repository_profile_sha256:
            raise ValueError("Attestor policy does not match the task binding")
        self.task = task
        self.profile = profile
        self.policy = policy
        self.artifact_store = artifact_store

    def attest(self, completion: RuntimeCompletion) -> PatchResult:
        identity = completion.identity
        if identity.task_id != self.task.task_id or identity.task_sha256 != canonical_sha256(
            self.task
        ):
            raise ValueError("Runtime completion belongs to another task")
        findings: list[_Finding] = []
        executions = self._executions(completion, findings)
        states = _StateIndex(completion.workspace_states)
        final_sha = completion.final_capture.diff_sha256 if completion.final_capture else None
        self._check_state_chain(completion, states, final_sha, findings)
        reproduction = self._reproduction(executions, states, final_sha, findings)
        checks = self._checks(executions, states, final_sha, findings)
        diff = self._diff(completion, findings)
        report = self._report(completion, executions, findings)

        snapshot = completion.snapshot
        failure = snapshot.failure
        outcome = snapshot.outcome
        blocking = [item for item in findings if item.severity is FindingSeverity.BLOCKING]
        if failure is None and blocking:
            category = min(blocking, key=lambda item: list(FindingCategory).index(item.category))
            failure = _CATEGORY_FAILURE[category.category]
            outcome = outcome_for_failure(failure)
        if failure is None and (
            reproduction is None
            or diff is None
            or any(item.status is not CheckStatus.PASSED for item in checks)
        ):
            failure = PatchForgeFailure.VALIDATION_FAILED
            outcome = outcome_for_failure(failure)
        if failure is None:
            outcome = PatchOutcome.PATCH_PROPOSED
        assert outcome is not None

        transitions = snapshot.transitions
        failure_transition = next(
            (item for item in transitions if item.kind is RuntimeTransitionKind.FAILURE),
            None,
        )
        return PatchResult(
            identity=identity,
            outcome=outcome,
            phase_reached=(
                failure_transition.source
                if failure_transition is not None
                else PatchForgePhase.CLOSED
            ),
            report=report,
            tool_calls=completion.tool_calls,
            executions=[item.evidence for item in executions],
            reproduction=reproduction,
            checks=checks,
            diff=diff,
            policy_findings=[
                self._finding(identity.run_id, index, item) for index, item in enumerate(findings)
            ],
            budget_usage=completion.budget_usage,
            failure=failure,
            started_at=transitions[0].occurred_at,
            completed_at=transitions[-1].occurred_at,
        )

    def _executions(
        self, completion: RuntimeCompletion, findings: list[_Finding]
    ) -> list[_Execution]:
        records = {item.call_id: item for item in completion.tool_calls}
        policy_sha = canonical_sha256(self.profile.sandbox)
        mapped: list[_Execution] = []
        for sandbox in completion.executions:
            record = records.get(sandbox.call_id)
            if record is None or record.tool_name not in EXECUTION_TOOLS:
                findings.append(
                    _Finding(
                        "execution_evidence_mismatch",
                        FindingSeverity.BLOCKING,
                        FindingCategory.TAMPER,
                        "Sandbox evidence does not belong to a recorded execution tool call.",
                    )
                )
                continue
            purpose = command_purpose_for(record.tool_name, record.phase)
            command = self.profile.commands.get(purpose)
            if command is None or canonical_sha256(command) != sandbox.command_sha256:
                findings.append(
                    _Finding(
                        "command_hash_mismatch",
                        FindingSeverity.BLOCKING,
                        FindingCategory.TAMPER,
                        f"Execution did not run the operator profile's {purpose} command.",
                    )
                )
                continue
            if sandbox.policy_sha256 != policy_sha:
                findings.append(
                    _Finding(
                        "sandbox_policy_mismatch",
                        FindingSeverity.BLOCKING,
                        FindingCategory.TAMPER,
                        "Execution did not use the operator profile's sandbox policy.",
                    )
                )
                continue
            status = _EXECUTION_STATUS[sandbox.status]
            mapped.append(
                _Execution(
                    record=record,
                    sandbox=sandbox,
                    purpose=purpose,
                    evidence=TestExecution(
                        execution_id=sandbox.execution_id,
                        run_id=sandbox.run_id,
                        tool_call_id=sandbox.call_id,
                        phase=record.phase,
                        check_kind=CheckKind(purpose.value),
                        command_name=purpose.value,
                        command_sha256=sandbox.command_sha256,
                        status=status,
                        exit_code=(
                            sandbox.exit_code
                            if status in {ExecutionStatus.PASSED, ExecutionStatus.FAILED}
                            else None
                        ),
                        started_at=sandbox.started_at,
                        completed_at=sandbox.completed_at,
                    ),
                )
            )
        return mapped

    def _check_state_chain(
        self,
        completion: RuntimeCompletion,
        states: _StateIndex,
        final_sha: str | None,
        findings: list[_Finding],
    ) -> None:
        records = completion.workspace_states
        if not records or records[0].kind is not WorkspaceStateKind.INITIAL:
            findings.append(
                _Finding(
                    "workspace_initial_state_missing",
                    FindingSeverity.BLOCKING,
                    FindingCategory.TAMPER,
                    "The run has no runtime fingerprint of its starting workspace.",
                )
            )
        elif records[0].changed_files:
            findings.append(
                _Finding(
                    "workspace_not_pristine",
                    FindingSeverity.BLOCKING,
                    FindingCategory.TAMPER,
                    "The workspace differed from the source commit before any tool call.",
                )
            )
        for previous, current in zip(records, records[1:], strict=False):
            explained = (
                previous.kind is WorkspaceStateKind.BEFORE_CALL
                and current.kind is WorkspaceStateKind.AFTER_CALL
                and previous.tool_call_sequence == current.tool_call_sequence
            )
            if not explained and previous.diff_sha256 != current.diff_sha256:
                findings.append(
                    _Finding(
                        "workspace_changed_out_of_band",
                        FindingSeverity.BLOCKING,
                        FindingCategory.TAMPER,
                        "The workspace changed between recorded tool calls.",
                    )
                )
                break
        for record in completion.tool_calls:
            if record.tool_name in STATE_TRACKED_TOOLS and not states.bracketed(record.sequence):
                findings.append(
                    _Finding(
                        "unfingerprinted_mutation",
                        FindingSeverity.BLOCKING,
                        FindingCategory.TAMPER,
                        "A write or execution call has no before/after workspace fingerprint.",
                    )
                )
                break
        if final_sha is not None and records and records[-1].diff_sha256 != final_sha:
            findings.append(
                _Finding(
                    "final_state_unobserved",
                    FindingSeverity.BLOCKING,
                    FindingCategory.TAMPER,
                    "The workspace changed after the last recorded tool call.",
                )
            )

    def _reproduction(
        self,
        executions: Sequence[_Execution],
        states: _StateIndex,
        final_sha: str | None,
        findings: list[_Finding],
    ) -> ReproductionEvidence | None:
        if CommandPurpose.REPRODUCTION not in self.profile.commands:
            return ReproductionEvidence(
                status=ReproductionStatus.NOT_PRACTICAL,
                rationale="The operator repository profile defines no reproduction command.",
            )
        before = next(
            (
                item
                for item in executions
                if item.purpose is CommandPurpose.REPRODUCTION
                and states.pristine_before(item.record.sequence)
                and item.evidence.status in {ExecutionStatus.PASSED, ExecutionStatus.FAILED}
            ),
            None,
        )
        if before is None:
            findings.append(
                _Finding(
                    "reproduction_missing",
                    FindingSeverity.BLOCKING,
                    FindingCategory.VALIDATION,
                    "No conclusive reproduction ran on the unmodified source tree.",
                )
            )
            return None
        if before.evidence.status is ExecutionStatus.PASSED:
            findings.append(
                _Finding(
                    "reproduction_not_demonstrated",
                    FindingSeverity.WARNING,
                    FindingCategory.VALIDATION,
                    "The reproduction command passed before any change.",
                )
            )
            return ReproductionEvidence(
                status=ReproductionStatus.PASS_BEFORE,
                before_execution=before.evidence,
                rationale="The reproduction command passed on the unmodified source tree.",
            )
        after = _latest_final(executions, CommandPurpose.TARGETED_TESTS, states, final_sha)
        if after is not None and after.evidence.status is ExecutionStatus.PASSED:
            return ReproductionEvidence(
                status=ReproductionStatus.FAIL_BEFORE_PASS_AFTER,
                before_execution=before.evidence,
                after_execution=after.evidence,
                rationale=(
                    "The reproduction command failed on the source tree and the targeted "
                    "tests passed on the final tree."
                ),
            )
        return ReproductionEvidence(
            status=ReproductionStatus.FAIL_BEFORE_NO_PASS,
            before_execution=before.evidence,
            after_execution=after.evidence if after is not None else None,
            rationale="The reproduction failed, but no targeted run passed on the final tree.",
        )

    def _checks(
        self,
        executions: Sequence[_Execution],
        states: _StateIndex,
        final_sha: str | None,
        findings: list[_Finding],
    ) -> list[CheckResult]:
        checks: list[CheckResult] = []
        for kind in (*_REQUIRED_CHECKS, *_OPTIONAL_CHECKS):
            purpose = CommandPurpose(kind.value)
            latest = _latest_final(executions, purpose, states, final_sha)
            if latest is not None:
                status = _CHECK_STATUS[latest.evidence.status]
                checks.append(
                    CheckResult(
                        check_kind=kind,
                        status=status,
                        execution_id=latest.evidence.execution_id,
                        summary=f"{purpose} {status} on the final tree.",
                    )
                )
                continue
            stale = any(item.purpose is purpose for item in executions)
            if stale:
                findings.append(
                    _Finding(
                        "stale_validation",
                        FindingSeverity.WARNING,
                        FindingCategory.VALIDATION,
                        f"{purpose} ran, but not on the final tree.",
                    )
                )
            if kind in _REQUIRED_CHECKS:
                checks.append(
                    CheckResult(
                        check_kind=kind,
                        status=CheckStatus.NOT_RUN,
                        summary=f"{purpose} did not run on the final tree.",
                    )
                )
            elif purpose in self.profile.commands:
                findings.append(
                    _Finding(
                        "optional_check_not_run",
                        FindingSeverity.INFO,
                        FindingCategory.VALIDATION,
                        f"The profile defines {purpose}, but it did not run on the final tree.",
                    )
                )
        return checks

    def _diff(self, completion: RuntimeCompletion, findings: list[_Finding]) -> DiffSummary | None:
        capture = completion.final_capture
        if capture is None:
            if completion.final_capture_error is FinalCaptureError.NO_CHANGES:
                findings.append(
                    _Finding(
                        "no_changes",
                        FindingSeverity.BLOCKING,
                        FindingCategory.VALIDATION,
                        "The reported workspace has no changes to propose.",
                    )
                )
            elif completion.final_capture_error is FinalCaptureError.WORKSPACE_ERROR:
                findings.append(
                    _Finding(
                        "final_capture_failed",
                        FindingSeverity.BLOCKING,
                        FindingCategory.TAMPER,
                        "The reported workspace could not be committed and attested.",
                    )
                )
            return None
        if sha256(capture.patch).hexdigest() != capture.diff_sha256:
            raise ValueError("Final capture patch does not match its hash")
        changed = capture.changed_files
        protected = [path for path in changed if path_matches(path, self.profile.protected_paths)]
        scope = [path for path in changed if not path_in_scope(path, self.task.scope_paths)]
        tests = [path for path in changed if is_test_path(path, self.profile)]
        sensitive = [path for path in changed if is_sensitive_path(path)]
        for code, paths, detail in (
            ("protected_path_changed", protected, "The patch changes an operator-protected path."),
            ("scope_violation", scope, "The patch changes a path outside the task scope."),
            ("sensitive_path_changed", sensitive, "The patch changes a sensitive path."),
            (
                "test_change_not_allowed",
                [] if self.policy.allow_test_file_changes else tests,
                "The patch changes test files, which the policy forbids.",
            ),
        ):
            if paths:
                findings.append(
                    _Finding(
                        code,
                        FindingSeverity.BLOCKING,
                        FindingCategory.POLICY,
                        f"{detail} ({len(paths)} path(s))",
                        paths[0],
                    )
                )
        if len(changed) > self.policy.max_changed_files:
            findings.append(
                _Finding(
                    "too_many_changed_files",
                    FindingSeverity.BLOCKING,
                    FindingCategory.POLICY,
                    f"The patch changes {len(changed)} files; the policy allows "
                    f"{self.policy.max_changed_files}.",
                )
            )
        if len(capture.patch) > self.policy.max_diff_bytes:
            findings.append(
                _Finding(
                    "diff_too_large",
                    FindingSeverity.BLOCKING,
                    FindingCategory.POLICY,
                    f"The patch is {len(capture.patch)} bytes; the policy allows "
                    f"{self.policy.max_diff_bytes}.",
                )
            )
        if capture.binary_files:
            findings.append(
                _Finding(
                    "binary_files_changed",
                    FindingSeverity.WARNING,
                    FindingCategory.POLICY,
                    f"The patch changes {len(capture.binary_files)} binary file(s).",
                    capture.binary_files[0],
                )
            )
        artifact = self.artifact_store.put(capture.patch, artifact_type=PATCH_ARTIFACT_TYPE)
        if artifact.sha256 != capture.diff_sha256:
            raise ArtifactStoreError("Stored patch artifact does not match the attested diff")
        return DiffSummary(
            base_sha=capture.base_sha,
            proposed_head_sha=capture.proposed_head_sha,
            diff_sha256=capture.diff_sha256,
            patch_artifact=artifact,
            changed_files=changed,
            additions=capture.additions,
            deletions=capture.deletions,
            binary_files=capture.binary_files[:100],
            protected_path_violations=protected[:100],
            scope_violations=scope[:100],
            test_files_changed=tests[:500],
        )

    @staticmethod
    def _report(
        completion: RuntimeCompletion,
        executions: Sequence[_Execution],
        findings: list[_Finding],
    ) -> AgentReport:
        report = completion.report
        known = {item.call_id for item in completion.tool_calls} | {
            item.evidence.execution_id for item in executions
        }
        if report is not None and set(report.evidence_references).issubset(known):
            return report
        if report is not None:
            findings.append(
                _Finding(
                    "report_evidence_unknown",
                    FindingSeverity.BLOCKING,
                    FindingCategory.TAMPER,
                    "The agent report references evidence the attestor cannot verify.",
                )
            )
        # A runtime-authored placeholder; it makes no validation claims.
        return AgentReport(
            summary="Runtime placeholder: no verifiable agent report was accepted.",
            hypothesis="Not provided by the agent.",
            implementation="Not provided by the agent.",
        )

    @staticmethod
    def _finding(run_id: UUID, index: int, finding: _Finding) -> PolicyFinding:
        return PolicyFinding(
            finding_id=uuid5(_FINDING_NAMESPACE, f"{run_id}:{index}:{finding.code}"),
            severity=finding.severity,
            code=finding.code,
            detail=finding.detail[:500],
            path=finding.path,
        )


class _StateIndex:
    def __init__(self, records: Sequence[WorkspaceStateRecord]) -> None:
        self.initial = next(
            (item for item in records if item.kind is WorkspaceStateKind.INITIAL), None
        )
        self.before = {
            item.tool_call_sequence: item
            for item in records
            if item.kind is WorkspaceStateKind.BEFORE_CALL
        }
        self.after = {
            item.tool_call_sequence: item
            for item in records
            if item.kind is WorkspaceStateKind.AFTER_CALL
        }

    def bracketed(self, sequence: int) -> bool:
        return sequence in self.before and sequence in self.after

    def pristine_before(self, sequence: int) -> bool:
        before = self.before.get(sequence)
        return (
            self.initial is not None
            and before is not None
            and before.changed_files == 0
            and before.diff_sha256 == self.initial.diff_sha256
        )

    def on_final_tree(self, sequence: int, final_sha: str | None) -> bool:
        before = self.before.get(sequence)
        after = self.after.get(sequence)
        return (
            final_sha is not None
            and before is not None
            and after is not None
            and before.diff_sha256 == after.diff_sha256 == final_sha
        )


def _latest_final(
    executions: Sequence[_Execution],
    purpose: CommandPurpose,
    states: _StateIndex,
    final_sha: str | None,
) -> _Execution | None:
    candidates = [
        item
        for item in executions
        if item.purpose is purpose and states.on_final_tree(item.record.sequence, final_sha)
    ]
    return max(candidates, key=lambda item: item.record.sequence, default=None)


__all__ = [
    "PATCH_ARTIFACT_TYPE",
    "ArtifactStore",
    "ArtifactStoreError",
    "FindingCategory",
    "LocalArtifactStore",
    "PatchForgeAttestor",
]
