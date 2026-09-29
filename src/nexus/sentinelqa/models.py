"""SentinelQA-lite contracts: pristine specification locks, independent runs, and verdicts.

SentinelQA validates a PatchForge candidate against the *original* specification tests,
never against tests the candidate may have changed. The ``SpecificationLock`` is the
verifiable pristine reference: it is derived from the Git objects of the exact source
commit before any engineering run, it names every file that counts as specification, and
it binds their content digests to the operator repository profile.

Everything here is runtime-attested. No model narrative enters these records.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, HttpUrl, StringConstraints, model_validator

from nexus.atlas.models import (
    ArtifactReference,
    CommitSha,
    Identifier,
    ReviewResult,
    ReviewVerdict,
    Sha256,
    StrictModel,
)
from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.models import ExecutionStatus, FindingSeverity, RepositoryPath
from nexus.patchforge.policy import CommandPurpose

SENTINELQA_SCHEMA_VERSION = 1
SENTINELQA_CONTRACT_VERSION: Literal["sentinelqa-contract-v1"] = "sentinelqa-contract-v1"
SENTINELQA_AGENT_ID: Literal["sentinelqa.reviewer"] = "sentinelqa.reviewer"
VERDICT_ARTIFACT_TYPE = "sentinelqa_verdict"
SentinelIssuer = Literal["sentinelqa.runtime"]
ShortNarrative = Annotated[str, StringConstraints(min_length=1, max_length=500)]
Narrative = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
FileMode = Literal["100644", "100755"]

# Files that decide how tests are collected, configured, or how Python starts. A change to
# any of them, anywhere in the tree, can redefine what "the tests pass" means, so they are
# locked with the specification and any change is blocking.
EVALUATION_CONFIG_NAMES = frozenset(
    {
        "conftest.py",
        "pytest.ini",
        ".pytest.ini",
        "tox.ini",
        "setup.cfg",
        "pyproject.toml",
        "sitecustomize.py",
        "usercustomize.py",
        # Modules that would shadow the test harness itself when placed on the import path.
        "pytest.py",
        "_pytest.py",
        "pluggy.py",
        "unittest.py",
        "doctest.py",
    }
)
EVALUATION_CONFIG_SUFFIXES = frozenset({".pth"})
EVALUATION_CONFIG_DIRECTORIES = frozenset(
    {"_pytest", "pytest", "pluggy", "unittest", "site-packages", "__pypackages__"}
)


class SpecificationKind(StrEnum):
    TEST = "test"
    EVALUATION_CONFIG = "evaluation_config"
    OPERATOR = "operator"


class SpecificationEntry(StrictModel):
    path: RepositoryPath
    kind: SpecificationKind
    mode: FileMode
    sha256: Sha256
    size_bytes: int = Field(ge=0)


class SpecificationLock(StrictModel):
    """The pristine specification set of one source commit under one operator profile."""

    schema_version: Literal[1] = 1
    contract_version: Literal["sentinelqa-contract-v1"] = SENTINELQA_CONTRACT_VERSION
    repository_url: HttpUrl
    source_sha: CommitSha
    profile_id: Identifier
    profile_sha256: Sha256
    test_path_prefixes: list[RepositoryPath] = Field(default_factory=list, max_length=50)
    entries: list[SpecificationEntry] = Field(default_factory=list, max_length=5000)
    captured_at: AwareDatetime
    attested_by: SentinelIssuer = "sentinelqa.runtime"

    @model_validator(mode="after")
    def validate_entries(self) -> SpecificationLock:
        paths = [item.path for item in self.entries]
        if paths != sorted(set(paths)):
            raise ValueError("Specification entries must be unique and sorted by path")
        return self

    @property
    def lock_sha256(self) -> str:
        """Identity of the locked specification, independent of when it was captured."""

        return canonical_sha256(self.model_dump(mode="json", exclude={"captured_at"}))

    @property
    def by_path(self) -> dict[str, SpecificationEntry]:
        return {item.path: item for item in self.entries}


class SpecificationCounts(StrictModel):
    """Counts parsed from a test runner's own summary; never estimated."""

    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    xfailed: int = Field(default=0, ge=0)
    xpassed: int = Field(default=0, ge=0)
    deselected: int = Field(default=0, ge=0)
    warnings: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        """Tests the runner accounted for, whatever their outcome."""

        return self.passed + self.failed + self.errors + self.skipped + self.xfailed + self.xpassed

    @property
    def not_passed(self) -> int:
        return self.total - self.passed


class SpecificationTree(StrEnum):
    PRISTINE = "pristine"
    VERIFICATION = "verification"


class RunnerCanaryRecord(StrictModel):
    """The runner-integrity canary SentinelQA planted for one run, and whether it was seen."""

    path: RepositoryPath
    sha256: Sha256
    reported_failed: bool
    """The runner's short summary named the canary as a failed test."""


class SpecificationRun(StrictModel):
    """One operator command executed by SentinelQA on a tree it built itself."""

    schema_version: Literal[1] = 1
    review_id: UUID
    sequence: int = Field(ge=1)
    tree: SpecificationTree
    purpose: CommandPurpose
    command_sha256: Sha256
    policy_sha256: Sha256
    status: ExecutionStatus
    exit_code: int | None = Field(default=None, ge=0, le=255)
    counts: SpecificationCounts | None = None
    tree_sha256_before: Sha256
    tree_sha256_after: Sha256
    stdout_sha256: Sha256
    stderr_sha256: Sha256
    output_truncated: bool
    started_at: AwareDatetime
    completed_at: AwareDatetime
    canary: RunnerCanaryRecord | None = None
    """Set on runner-integrity probes: the full suite with a planted failing test."""
    attested_by: SentinelIssuer = "sentinelqa.runtime"

    @model_validator(mode="after")
    def validate_run(self) -> SpecificationRun:
        if self.completed_at < self.started_at:
            raise ValueError("Specification run completion cannot precede start")
        if self.canary is not None and self.purpose is not CommandPurpose.FULL_TEST_SUITE:
            raise ValueError("Only the full-suite command carries a runner-integrity canary")
        if self.status is ExecutionStatus.PASSED and self.exit_code != 0:
            raise ValueError("Passed specification run requires exit code zero")
        if self.status is ExecutionStatus.FAILED and (
            self.exit_code is None or self.exit_code == 0
        ):
            raise ValueError("Failed specification run requires a nonzero exit code")
        if self.status in {ExecutionStatus.TIMED_OUT, ExecutionStatus.ERROR} and (
            self.exit_code is not None
        ):
            raise ValueError("Exceptional specification run cannot claim an exit code")
        return self

    @property
    def tree_unchanged(self) -> bool:
        return self.tree_sha256_before == self.tree_sha256_after

    @property
    def runner_integrity_demonstrated(self) -> bool:
        """The runner reported the planted canary, and nothing else, as a failure."""

        return (
            self.canary is not None
            and self.canary.reported_failed
            and self.status is ExecutionStatus.FAILED
            and self.exit_code == 1
            and self.counts is not None
            and self.counts.failed == 1
            and self.counts.errors == 0
            and self.tree_unchanged
        )


class FindingCategory(StrEnum):
    """What a finding means for the verdict."""

    REJECTION = "rejection"
    """The candidate is rejected on evidence."""

    UNVERIFIABLE = "unverifiable"
    """SentinelQA could not establish trustworthy evidence; the verdict is inconclusive."""

    ADVISORY = "advisory"
    """Recorded for reviewers; does not decide the verdict."""


class SentinelFindingCode(StrEnum):
    # Rejections: the candidate redefined or evaded the specification, or fails it.
    SPECIFICATION_MODIFIED = "specification_modified"
    SPECIFICATION_DELETED = "specification_deleted"
    SPECIFICATION_NOT_REGULAR_FILE = "specification_not_regular_file"
    EVALUATION_CONFIG_ALTERED = "evaluation_config_altered"
    PROTECTED_PATH_CHANGED = "protected_path_changed"
    SENSITIVE_PATH_CHANGED = "sensitive_path_changed"
    PATCH_APPLY_FAILED = "patch_apply_failed"
    PRISTINE_TESTS_FAILED = "pristine_tests_failed"
    TESTS_SKIPPED = "tests_skipped"
    TESTS_MISSING = "tests_missing"
    VERIFICATION_TREE_MUTATED = "verification_tree_mutated"
    HARNESS_TAMPERING = "harness_tampering"
    RUNNER_INTEGRITY_VIOLATED = "runner_integrity_violated"
    INDEPENDENT_VALIDATION_DISAGREES = "independent_validation_disagrees"
    UNRECORDED_CHANGE = "unrecorded_change"
    NO_CODE_CHANGE = "no_code_change"
    # Unverifiable: fail closed without a verdict on the candidate itself.
    NOT_A_PROPOSAL = "not_a_proposal"
    IDENTITY_MISMATCH = "identity_mismatch"
    PRISTINE_REFERENCE_UNVERIFIABLE = "pristine_reference_unverifiable"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    EXECUTOR_UNAVAILABLE = "executor_unavailable"
    RUNNER_INTEGRITY_UNPROVEN = "runner_integrity_unproven"
    # Advisory.
    REPRODUCTION_NOT_DEMONSTRATED = "reproduction_not_demonstrated"
    CANDIDATE_TESTS_EXCLUDED = "candidate_tests_excluded"


FINDING_CATEGORY: dict[SentinelFindingCode, FindingCategory] = {
    SentinelFindingCode.SPECIFICATION_MODIFIED: FindingCategory.REJECTION,
    SentinelFindingCode.SPECIFICATION_DELETED: FindingCategory.REJECTION,
    SentinelFindingCode.SPECIFICATION_NOT_REGULAR_FILE: FindingCategory.REJECTION,
    SentinelFindingCode.EVALUATION_CONFIG_ALTERED: FindingCategory.REJECTION,
    SentinelFindingCode.PROTECTED_PATH_CHANGED: FindingCategory.REJECTION,
    SentinelFindingCode.SENSITIVE_PATH_CHANGED: FindingCategory.REJECTION,
    SentinelFindingCode.PATCH_APPLY_FAILED: FindingCategory.REJECTION,
    SentinelFindingCode.PRISTINE_TESTS_FAILED: FindingCategory.REJECTION,
    SentinelFindingCode.TESTS_SKIPPED: FindingCategory.REJECTION,
    SentinelFindingCode.TESTS_MISSING: FindingCategory.REJECTION,
    SentinelFindingCode.VERIFICATION_TREE_MUTATED: FindingCategory.REJECTION,
    SentinelFindingCode.HARNESS_TAMPERING: FindingCategory.REJECTION,
    SentinelFindingCode.RUNNER_INTEGRITY_VIOLATED: FindingCategory.REJECTION,
    SentinelFindingCode.INDEPENDENT_VALIDATION_DISAGREES: FindingCategory.REJECTION,
    SentinelFindingCode.UNRECORDED_CHANGE: FindingCategory.REJECTION,
    SentinelFindingCode.NO_CODE_CHANGE: FindingCategory.REJECTION,
    SentinelFindingCode.NOT_A_PROPOSAL: FindingCategory.UNVERIFIABLE,
    SentinelFindingCode.IDENTITY_MISMATCH: FindingCategory.UNVERIFIABLE,
    SentinelFindingCode.PRISTINE_REFERENCE_UNVERIFIABLE: FindingCategory.UNVERIFIABLE,
    SentinelFindingCode.EVIDENCE_INCOMPLETE: FindingCategory.UNVERIFIABLE,
    SentinelFindingCode.EXECUTOR_UNAVAILABLE: FindingCategory.UNVERIFIABLE,
    SentinelFindingCode.RUNNER_INTEGRITY_UNPROVEN: FindingCategory.UNVERIFIABLE,
    SentinelFindingCode.REPRODUCTION_NOT_DEMONSTRATED: FindingCategory.ADVISORY,
    SentinelFindingCode.CANDIDATE_TESTS_EXCLUDED: FindingCategory.ADVISORY,
}


class SentinelFinding(StrictModel):
    finding_id: UUID
    code: SentinelFindingCode
    category: FindingCategory
    severity: FindingSeverity
    detail: ShortNarrative
    path: RepositoryPath | None = None
    attested_by: SentinelIssuer = "sentinelqa.runtime"

    @model_validator(mode="after")
    def validate_category(self) -> SentinelFinding:
        if FINDING_CATEGORY[self.code] is not self.category:
            raise ValueError("Finding category does not match its code")
        blocking = self.category is not FindingCategory.ADVISORY
        if blocking != (self.severity is FindingSeverity.BLOCKING):
            raise ValueError("Only advisory findings may be non-blocking")
        return self


class SpecificationIntegrity(StrictModel):
    """How the candidate tree compares with the locked specification."""

    locked_entries: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    modified: list[RepositoryPath] = Field(default_factory=list, max_length=500)
    deleted: list[RepositoryPath] = Field(default_factory=list, max_length=500)
    not_regular: list[RepositoryPath] = Field(default_factory=list, max_length=500)
    added_tests_excluded: list[RepositoryPath] = Field(default_factory=list, max_length=500)
    evaluation_config_changes: list[RepositoryPath] = Field(default_factory=list, max_length=500)
    restored: list[RepositoryPath] = Field(default_factory=list, max_length=500)
    candidate_changed_files: list[RepositoryPath] = Field(default_factory=list, max_length=1000)
    verification_changed_files: list[RepositoryPath] = Field(default_factory=list, max_length=1000)
    """What remains of the candidate once the pristine specification is restored."""

    @model_validator(mode="after")
    def validate_counts(self) -> SpecificationIntegrity:
        accounted = self.unchanged + len(self.modified) + len(self.deleted) + len(self.not_regular)
        if accounted != self.locked_entries:
            raise ValueError("Specification integrity counts do not cover every locked entry")
        return self

    @property
    def specification_intact(self) -> bool:
        return not (
            self.modified or self.deleted or self.not_regular or self.evaluation_config_changes
        )


class SentinelVerdict(StrictModel):
    """SentinelQA's runtime-attested review of one PatchForge candidate."""

    schema_version: Literal[1] = 1
    contract_version: Literal["sentinelqa-contract-v1"] = SENTINELQA_CONTRACT_VERSION
    review_id: UUID
    reviewer_agent_id: Literal["sentinelqa.reviewer"] = SENTINELQA_AGENT_ID
    task_id: UUID
    patchforge_run_id: UUID
    atlas_job_id: UUID
    source_sha: CommitSha
    lock_sha256: Sha256
    patch_sha256: Sha256 | None = None
    proposed_head_sha: CommitSha | None = None
    verdict: ReviewVerdict
    summary: Narrative
    findings: list[SentinelFinding] = Field(default_factory=list, max_length=1000)
    integrity: SpecificationIntegrity | None = None
    runs: list[SpecificationRun] = Field(default_factory=list, max_length=20)
    patchforge_checks_agree: bool | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime
    attested_by: SentinelIssuer = "sentinelqa.runtime"

    @model_validator(mode="after")
    def validate_verdict(self) -> SentinelVerdict:
        if self.completed_at < self.started_at:
            raise ValueError("Verdict completion cannot precede start")
        if len({item.finding_id for item in self.findings}) != len(self.findings):
            raise ValueError("Finding IDs must be unique")
        if any(item.review_id != self.review_id for item in self.runs):
            raise ValueError("Specification run belongs to another review")
        if [item.sequence for item in self.runs] != list(range(1, len(self.runs) + 1)):
            raise ValueError("Specification runs must be contiguous and ordered")
        categories = {item.category for item in self.findings}
        expected = verdict_for(categories)
        if self.verdict is not expected:
            raise ValueError(f"Verdict {self.verdict} does not follow from its findings")
        if self.verdict is ReviewVerdict.PASSED:
            if self.integrity is None or not self.integrity.specification_intact:
                raise ValueError("A passed verdict requires an intact specification")
            required = {CommandPurpose.TARGETED_TESTS, CommandPurpose.FULL_TEST_SUITE}
            verified = {
                item.purpose
                for item in self.runs
                if item.tree is SpecificationTree.VERIFICATION
                and item.canary is None
                and item.status is ExecutionStatus.PASSED
                and item.tree_unchanged
                and item.counts is not None
                and item.counts.failed == 0
                and item.counts.errors == 0
                and item.counts.passed >= 1
            }
            if not required.issubset(verified):
                raise ValueError(
                    "A passed verdict requires passing pristine targeted and full runs"
                )
            if not any(
                item.tree is SpecificationTree.VERIFICATION and item.runner_integrity_demonstrated
                for item in self.runs
            ):
                raise ValueError("A passed verdict requires demonstrated runner integrity")
            if self.patch_sha256 is None or self.proposed_head_sha is None:
                raise ValueError("A passed verdict must name the candidate it verified")
        return self

    @property
    def verdict_sha256(self) -> str:
        return canonical_sha256(self)

    @property
    def finding_codes(self) -> list[str]:
        return [item.code.value for item in self.findings]

    def to_atlas_review(self, evidence: ArtifactReference) -> ReviewResult:
        """Atlas' outer review contract, citing the stored verdict artifact as evidence."""

        if evidence.sha256 != self.verdict_sha256:
            raise ValueError("Review evidence does not reference this verdict")
        return ReviewResult(
            review_id=self.review_id,
            reviewer_agent_id=self.reviewer_agent_id,
            verdict=self.verdict,
            summary=self.summary,
            evidence=[evidence],
            created_at=self.completed_at,
        )


def verdict_for(categories: set[FindingCategory]) -> ReviewVerdict:
    """Unverifiable evidence outranks rejection; only a clean review passes."""

    if FindingCategory.UNVERIFIABLE in categories:
        return ReviewVerdict.INCONCLUSIVE
    if FindingCategory.REJECTION in categories:
        return ReviewVerdict.FAILED
    return ReviewVerdict.PASSED


def is_evaluation_config_path(path: str) -> bool:
    """Configuration and start-up hooks that shape how the specification is evaluated,
    plus any module or package that would shadow the test harness on the import path."""

    parts = [part for part in path.split("/") if part]
    if not parts:
        return False
    name = parts[-1]
    return (
        name in EVALUATION_CONFIG_NAMES
        or any(name.endswith(suffix) for suffix in EVALUATION_CONFIG_SUFFIXES)
        or any(part in EVALUATION_CONFIG_DIRECTORIES for part in parts[:-1])
    )


__all__ = [
    "EVALUATION_CONFIG_DIRECTORIES",
    "EVALUATION_CONFIG_NAMES",
    "EVALUATION_CONFIG_SUFFIXES",
    "FINDING_CATEGORY",
    "SENTINELQA_AGENT_ID",
    "SENTINELQA_CONTRACT_VERSION",
    "SENTINELQA_SCHEMA_VERSION",
    "VERDICT_ARTIFACT_TYPE",
    "FindingCategory",
    "RunnerCanaryRecord",
    "SentinelFinding",
    "SentinelFindingCode",
    "SentinelVerdict",
    "SpecificationCounts",
    "SpecificationEntry",
    "SpecificationIntegrity",
    "SpecificationKind",
    "SpecificationLock",
    "SpecificationRun",
    "SpecificationTree",
    "is_evaluation_config_path",
    "verdict_for",
]
