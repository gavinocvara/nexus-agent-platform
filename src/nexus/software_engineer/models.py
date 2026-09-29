"""Strict contracts for the resident Software Engineer.

The resident engineer is a governed daily cycle over this repository. These contracts make
its observations, candidates, risk assessments, self-review, decisions, approval requests,
budgets, and reports typed and auditable. Everything an agent might narrate is separated
from what the runtime attests: validation gates, budgets, and decisions are runtime-owned.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from nexus.atlas.models import ActorIdentity, ActorType, CommitSha, Identifier, Sha256, StrictModel
from nexus.patchforge.models import RepositoryPath

SOFTWARE_ENGINEER_AGENT_ID: Literal["software_engineer.resident"] = "software_engineer.resident"
SOFTWARE_ENGINEER_REVIEWER_ID: Literal["software_engineer.reviewer"] = "software_engineer.reviewer"
SOFTWARE_ENGINEER_CONTRACT_VERSION: Literal["software-engineer-contract-v1"] = (
    "software-engineer-contract-v1"
)
EngineerIssuer = Literal["software_engineer.runtime"]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
Narrative = Annotated[str, StringConstraints(min_length=1, max_length=4000)]
Percent = Annotated[int, Field(ge=0, le=100)]


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}[self]

    def raised(self) -> RiskLevel:
        """One level higher; HIGH stays HIGH."""

        return {RiskLevel.LOW: RiskLevel.MEDIUM, RiskLevel.MEDIUM: RiskLevel.HIGH}.get(self, self)


def highest(*levels: RiskLevel) -> RiskLevel:
    return max(levels, key=lambda level: level.rank)


class ChangeCategory(StrEnum):
    """Closed vocabulary of engineering change kinds with an intrinsic risk floor."""

    # Low: isolated, mechanical, or behavior-preserving.
    DOCUMENTATION_CORRECTION = "documentation_correction"
    TEST_REPAIR = "test_repair"
    MICRO_BUG_FIX = "micro_bug_fix"
    DEFENSIVE_CHECK = "defensive_check"
    FORMATTING = "formatting"
    TYPE_ANNOTATION = "type_annotation"
    DEAD_CODE_REMOVAL = "dead_code_removal"
    MICRO_REFACTOR = "micro_refactor"
    # Medium: behavior, dependencies, evaluation, or model-facing changes.
    BEHAVIOR_CHANGE = "behavior_change"
    REFACTOR = "refactor"
    DEPENDENCY_UPDATE = "dependency_update"
    PERFORMANCE_CHANGE = "performance_change"
    PROMPT_CHANGE = "prompt_change"
    TOOL_SELECTION_CHANGE = "tool_selection_change"
    EVALUATOR_CHANGE = "evaluator_change"
    PERSISTENT_STATE_CHANGE = "persistent_state_change"
    MODEL_BEHAVIOR_CHANGE = "model_behavior_change"
    API_CHANGE = "api_change"
    # High: security, data, infrastructure, spending, and the engineer's own authority.
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    SECRETS = "secrets"
    SECURITY_BOUNDARY = "security_boundary"
    DATA_MIGRATION = "data_migration"
    SCHEMA_CHANGE = "schema_change"
    INFRASTRUCTURE = "infrastructure"
    DEPLOYMENT_CONFIGURATION = "deployment_configuration"
    SPENDING_CONTROL = "spending_control"
    DATA_DELETION = "data_deletion"
    AUTONOMY_LIMIT = "autonomy_limit"
    APPROVAL_POLICY = "approval_policy"
    TOOL_ACCESS_EXPANSION = "tool_access_expansion"
    SELF_GOVERNANCE = "self_governance"
    BENCHMARK_THRESHOLD = "benchmark_threshold"
    SAFETY_CONTROL = "safety_control"
    UNKNOWN = "unknown"


_LOW_CATEGORIES = frozenset(
    {
        ChangeCategory.DOCUMENTATION_CORRECTION,
        ChangeCategory.TEST_REPAIR,
        ChangeCategory.MICRO_BUG_FIX,
        ChangeCategory.DEFENSIVE_CHECK,
        ChangeCategory.FORMATTING,
        ChangeCategory.TYPE_ANNOTATION,
        ChangeCategory.DEAD_CODE_REMOVAL,
        ChangeCategory.MICRO_REFACTOR,
    }
)
_MEDIUM_CATEGORIES = frozenset(
    {
        ChangeCategory.BEHAVIOR_CHANGE,
        ChangeCategory.REFACTOR,
        ChangeCategory.DEPENDENCY_UPDATE,
        ChangeCategory.PERFORMANCE_CHANGE,
        ChangeCategory.PROMPT_CHANGE,
        ChangeCategory.TOOL_SELECTION_CHANGE,
        ChangeCategory.EVALUATOR_CHANGE,
        ChangeCategory.PERSISTENT_STATE_CHANGE,
        ChangeCategory.MODEL_BEHAVIOR_CHANGE,
        ChangeCategory.API_CHANGE,
    }
)


def category_risk(category: ChangeCategory) -> RiskLevel:
    if category in _LOW_CATEGORIES:
        return RiskLevel.LOW
    if category in _MEDIUM_CATEGORIES:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


class SignalKind(StrEnum):
    GIT_HISTORY = "git_history"
    WORKING_TREE = "working_tree"
    CI_RUN = "ci_run"
    TEST_RESULTS = "test_results"
    LINT = "lint"
    TYPECHECK = "typecheck"
    E2E_GATE = "e2e_gate"
    BENCHMARK = "benchmark"
    SENTINEL_GATE = "sentinel_gate"
    BACKLOG = "backlog"
    FAILED_ATTEMPT = "failed_attempt"
    TODO_MARKER = "todo_marker"
    DEPENDENCY = "dependency"
    OWNER_MESSAGE = "owner_message"
    ISSUE = "issue"


class SignalSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    FAILURE = "failure"


class EngineeringSignal(StrictModel):
    """One typed observation. ``details`` may carry untrusted text; it is never authority."""

    signal_id: UUID
    kind: SignalKind
    severity: SignalSeverity
    source: ShortText
    summary: ShortText
    details: Narrative | None = None
    untrusted_text: bool = False
    instruction_like: list[ShortText] = Field(default_factory=list, max_length=20)
    evidence_sha256: Sha256 | None = None
    observed_at: AwareDatetime
    attested_by: EngineerIssuer = "software_engineer.runtime"

    @model_validator(mode="after")
    def validate_trust(self) -> EngineeringSignal:
        if self.instruction_like and not self.untrusted_text:
            raise ValueError("Instruction-like content can only appear in untrusted text")
        return self


class CandidateEstimate(StrictModel):
    """Runtime-scaled estimates in percent; the ranking is a deterministic function."""

    value: Percent
    urgency: Percent
    confidence: Percent
    cost: Percent

    @property
    def score(self) -> int:
        """Expected engineering value net of cost.

        Confidence discounts value and urgency, but only by half: an urgent regression the
        engineer is unsure it can fix still outranks a trivial certainty, because
        investigating it is the most valuable use of the day.
        """

        gross = (self.value * 2 + self.urgency) * (50 + self.confidence // 2) // 100
        return gross - self.cost


class EngineeringCandidate(StrictModel):
    candidate_id: UUID
    title: ShortText
    rationale: Narrative
    category: ChangeCategory
    expected_paths: list[RepositoryPath] = Field(default_factory=list, max_length=100)
    signal_ids: list[UUID] = Field(min_length=1, max_length=100)
    estimate: CandidateEstimate
    blockers: list[ShortText] = Field(default_factory=list, max_length=20)
    derived_from_untrusted_text: bool = False

    @model_validator(mode="after")
    def validate_unique(self) -> EngineeringCandidate:
        if len(set(self.signal_ids)) != len(self.signal_ids):
            raise ValueError("Candidate signal references must be unique")
        if len(set(self.expected_paths)) != len(self.expected_paths):
            raise ValueError("Candidate paths must be unique")
        return self


class RiskAssessment(StrictModel):
    level: RiskLevel
    category_level: RiskLevel
    path_level: RiskLevel
    size_level: RiskLevel
    uncertain: bool
    governing_paths: list[RepositoryPath] = Field(default_factory=list, max_length=100)
    reasons: list[ShortText] = Field(min_length=1, max_length=50)
    attested_by: EngineerIssuer = "software_engineer.runtime"

    @model_validator(mode="after")
    def validate_monotonic(self) -> RiskAssessment:
        floor = highest(self.category_level, self.path_level, self.size_level)
        if self.uncertain:
            floor = floor.raised()
        if self.level.rank < floor.rank:
            raise ValueError("Risk level cannot be lower than its components imply")
        if self.governing_paths and self.level is not RiskLevel.HIGH:
            raise ValueError("Touching a governing path is always high risk")
        return self


class CycleMode(StrEnum):
    DRY_RUN = "dry_run"
    """Observe, prioritize, plan, and report; never change code."""

    PROPOSE = "propose"
    """Produce validated candidates as approval requests; never ship autonomously."""

    AUTONOMOUS_LOW_RISK = "autonomous_low_risk"
    """Additionally ship low-risk changes that pass every required gate."""


class CyclePhase(StrEnum):
    CREATED = "created"
    OBSERVE = "observe"
    UNDERSTAND = "understand"
    PRIORITIZE = "prioritize"
    INVESTIGATE = "investigate"
    PLAN = "plan"
    IMPLEMENT = "implement"
    TEST = "test"
    SELF_REVIEW = "self_review"
    ASSESS_RISK = "assess_risk"
    DECIDE = "decide"
    OBSERVE_RESULTS = "observe_results"
    LEARN = "learn"
    REPORT = "report"
    CLOSED = "closed"


class CycleDecision(StrEnum):
    NO_WORK = "no_work"
    SHIP = "ship"
    REQUEST_APPROVAL = "request_approval"
    ABANDON = "abandon"
    BLOCKED = "blocked"


class CycleFailure(StrEnum):
    BUDGET_EXHAUSTED = "budget_exhausted"
    CREDENTIALS_MISSING = "credentials_missing"
    EXECUTOR_ERROR = "executor_error"
    VALIDATION_FAILED = "validation_failed"
    POLICY_DENIED = "policy_denied"
    INTERNAL_ERROR = "internal_error"


class ValidationGate(StrEnum):
    RUFF_FORMAT = "ruff_format"
    RUFF_LINT = "ruff_lint"
    MYPY = "mypy"
    PYTEST_TARGETED = "pytest_targeted"
    PYTEST_FULL = "pytest_full"
    E2E_GATE = "e2e_gate"
    BENCHMARK_GATE = "benchmark_gate"
    SENTINEL_GATE = "sentinel_gate"
    SENTINEL_REVIEW = "sentinel_review"
    SCENARIOS = "scenarios"
    COMPOSE_CONFIG = "compose_config"


class GateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    NOT_RUN = "not_run"


class GateResult(StrictModel):
    gate: ValidationGate
    status: GateStatus
    summary: ShortText
    evidence_sha256: Sha256 | None = None
    attested_by: EngineerIssuer = "software_engineer.runtime"

    @model_validator(mode="after")
    def validate_evidence(self) -> GateResult:
        if self.status in {GateStatus.PASSED, GateStatus.FAILED} and self.evidence_sha256 is None:
            raise ValueError("A gate that ran must cite its evidence")
        return self


class ReviewQuestion(StrEnum):
    ROOT_CAUSE = "root_cause_not_symptom"
    OTHER_PATHS = "no_other_path_broken"
    PUBLIC_BEHAVIOR = "public_behavior_unchanged"
    INVARIANT = "no_invariant_weakened"
    TEST_WEAKENED = "no_test_weakened"
    RACE = "no_race_introduced"
    NONDETERMINISM = "no_nondeterminism_introduced"
    COMPLEXITY = "no_unnecessary_complexity"
    HIDDEN_STATE = "no_hidden_state"
    SENSITIVE_DATA = "no_sensitive_data_exposed"
    PROMPT_INJECTION = "no_prompt_injection_risk"
    TESTS_COVER = "tests_cover_the_change"
    SMALLER = "no_smaller_solution"
    HUMAN_REVIEW = "human_review_not_needed"


CRITICAL_REVIEW_QUESTIONS = frozenset(
    {
        ReviewQuestion.INVARIANT,
        ReviewQuestion.TEST_WEAKENED,
        ReviewQuestion.SENSITIVE_DATA,
        ReviewQuestion.PROMPT_INJECTION,
        ReviewQuestion.HUMAN_REVIEW,
    }
)


class ReviewAnswer(StrEnum):
    CLEAR = "clear"
    CONCERN = "concern"
    UNKNOWN = "unknown"


class ReviewItem(StrictModel):
    question: ReviewQuestion
    answer: ReviewAnswer
    note: ShortText


class SelfReview(StrictModel):
    """Produced by a reviewer component that never shares state with the implementer."""

    reviewer: Literal["software_engineer.reviewer"] = SOFTWARE_ENGINEER_REVIEWER_ID
    items: list[ReviewItem] = Field(min_length=len(ReviewQuestion), max_length=len(ReviewQuestion))
    reviewed_diff_sha256: Sha256 | None = None
    attested_by: EngineerIssuer = "software_engineer.runtime"

    @model_validator(mode="after")
    def validate_complete(self) -> SelfReview:
        if {item.question for item in self.items} != set(ReviewQuestion):
            raise ValueError("Self-review must answer every question exactly once")
        return self

    @property
    def concerns(self) -> list[ReviewItem]:
        return [item for item in self.items if item.answer is not ReviewAnswer.CLEAR]

    @property
    def blocking(self) -> bool:
        """A concern or an unknown on a critical question blocks autonomous shipping."""

        return any(
            item.question in CRITICAL_REVIEW_QUESTIONS and item.answer is not ReviewAnswer.CLEAR
            for item in self.items
        )

    @property
    def requires_human(self) -> bool:
        """Any concern or unknown, on any question, needs the owner. A concern is the
        reviewer finding a problem, so it can never weigh less than an unknown."""

        return self.blocking or bool(self.concerns)


class PublishedChange(StrictModel):
    """Runtime evidence that a validated change became a draft pull request.

    The engineer never merges: a publication is a branch plus a draft pull request that a
    human reviews. The remote tree is verified to equal the locally validated tree before
    the reference is created, so what the owner approved is what reviewers see.
    """

    publication_id: UUID
    cycle_id: UUID
    request_id: UUID | None = None
    decision_id: UUID | None = None
    authority: Literal["owner_decision", "autonomous_low_risk", "integration_exercise"]
    """``integration_exercise``: an owner-run check of the publisher itself, publishing a
    purpose-built harmless file and withdrawing it; never a change the engineer made."""
    provider: Literal["github"]
    repository: Annotated[str, StringConstraints(min_length=3, max_length=200)]
    base_branch: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    base_sha: CommitSha
    base_head_at_publish: CommitSha
    branch: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    tree_sha: CommitSha
    local_commit_sha: CommitSha
    remote_commit_sha: CommitSha
    diff_sha256: Sha256
    pull_request_number: int = Field(ge=1)
    pull_request_url: Annotated[str, StringConstraints(pattern=r"^https://", max_length=500)]
    draft: Literal[True] = True
    published_by: ActorIdentity
    published_at: AwareDatetime
    attested_by: EngineerIssuer = "software_engineer.runtime"

    @model_validator(mode="after")
    def validate_authority(self) -> PublishedChange:
        if self.authority == "owner_decision":
            if self.request_id is None or self.decision_id is None:
                raise ValueError("A publication under an owner decision names the decision")
            if self.published_by.actor_type is not ActorType.HUMAN:
                raise ValueError("An owner-decided publication is published by the human owner")
        elif self.authority == "integration_exercise":
            if self.request_id is not None or self.decision_id is not None:
                raise ValueError("An integration exercise answers no approval request")
            if self.published_by.actor_type is not ActorType.HUMAN:
                raise ValueError("An integration exercise is run by the human owner")
        elif self.published_by.actor_type is not ActorType.AGENT:
            raise ValueError("An autonomous publication is published by the agent")
        return self


class ChangeSummary(StrictModel):
    """Runtime-owned description of a produced change; never authored by a model."""

    base_sha: CommitSha
    branch: Annotated[str, StringConstraints(min_length=1, max_length=255)] | None = None
    commit_sha: CommitSha | None = None
    changed_files: list[RepositoryPath] = Field(min_length=1, max_length=1000)
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    diff_bytes: int = Field(ge=0)
    diff_sha256: Sha256
    patch_result_sha256: Sha256 | None = None
    sentinel_verdict_sha256: Sha256 | None = None
    rollback_reference: ShortText
    publication: PublishedChange | None = None

    @model_validator(mode="after")
    def validate_unique(self) -> ChangeSummary:
        if len(set(self.changed_files)) != len(self.changed_files):
            raise ValueError("Changed files must be unique")
        if self.publication is not None:
            if self.publication.base_sha != self.base_sha:
                raise ValueError("A publication must build on the change's base")
            if self.publication.diff_sha256 != self.diff_sha256:
                raise ValueError("A publication must carry the change's diff")
            if self.commit_sha is not None and self.publication.local_commit_sha != self.commit_sha:
                raise ValueError("A publication must publish the change's commit")
        return self


class ApprovalRequest(StrictModel):
    """Everything the owner needs to decide without reading the diff."""

    request_id: UUID
    cycle_id: UUID
    candidate_id: UUID
    title: ShortText
    problem: Narrative
    root_cause: Narrative
    proposed_fix: Narrative
    why: Narrative
    files_affected: list[RepositoryPath] = Field(default_factory=list, max_length=1000)
    behavior_changed: Narrative
    validation: list[GateResult] = Field(default_factory=list, max_length=50)
    benchmark_impact: ShortText
    security_impact: ShortText
    risk: RiskAssessment
    confidence: Percent
    rollback_plan: Narrative
    reference: ShortText
    question: ShortText
    recommendation: Literal["ship", "revise", "reject"]
    dry_run: bool
    created_at: AwareDatetime
    attested_by: EngineerIssuer = "software_engineer.runtime"


class OwnerVerdict(StrEnum):
    SHIP = "ship"
    REVISE = "revise"
    REJECT = "reject"


class OwnerDecision(StrictModel):
    """Only a human owner decides; silence is never a decision."""

    decision_id: UUID
    request_id: UUID
    verdict: OwnerVerdict
    decided_by: ActorIdentity
    reason: ShortText
    channel: Literal["slack", "cli", "github"]
    decided_at: AwareDatetime

    @model_validator(mode="after")
    def require_human(self) -> OwnerDecision:
        if self.decided_by.actor_type is not ActorType.HUMAN:
            raise ValueError("Owner decisions require a human actor")
        return self


class CycleBudget(StrictModel):
    max_runtime_seconds: int = Field(ge=60, le=86_400)
    max_turns: int = Field(ge=1, le=1000)
    max_tool_calls: int = Field(ge=1, le=10_000)
    max_model_calls: int = Field(ge=0, le=1000)
    max_input_tokens: int = Field(ge=0, le=50_000_000)
    max_output_tokens: int = Field(ge=0, le=5_000_000)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_changed_files: int = Field(ge=1, le=100)
    max_diff_bytes: int = Field(ge=1, le=10_000_000)


class CycleUsage(StrictModel):
    runtime_seconds: float = Field(default=0, ge=0)
    turns: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    changed_files: int = Field(default=0, ge=0)
    diff_bytes: int = Field(default=0, ge=0)

    def exceeded(self, budget: CycleBudget) -> list[str]:
        """Names of the hard budget dimensions this usage exceeds (empty means within budget).

        Changed files and diff bytes are autonomous-shipping limits, not hard budgets: a
        larger change is classified as higher risk and proposed to the owner instead.
        """

        exceeded: list[str] = []
        pairs: list[tuple[str, float, float | None]] = [
            ("runtime_seconds", self.runtime_seconds, budget.max_runtime_seconds),
            ("turns", self.turns, budget.max_turns),
            ("tool_calls", self.tool_calls, budget.max_tool_calls),
            ("model_calls", self.model_calls, budget.max_model_calls),
            ("input_tokens", self.input_tokens, budget.max_input_tokens),
            ("output_tokens", self.output_tokens, budget.max_output_tokens),
            ("cost_usd", self.cost_usd or 0.0, budget.max_cost_usd),
        ]
        for name, used, limit in pairs:
            if limit is not None and used > limit:
                exceeded.append(name)
        return exceeded


class CycleTransition(StrictModel):
    sequence: int = Field(ge=1)
    source: CyclePhase
    target: CyclePhase
    reason: ShortText
    occurred_at: AwareDatetime


class NotificationEvent(StrEnum):
    DAILY_REPORT = "daily_report"
    BUG_FIXED = "bug_fixed"
    IMPROVEMENT_COMPLETED = "improvement_completed"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"
    TEST_REGRESSION = "test_regression"
    BENCHMARK_REGRESSION = "benchmark_regression"
    SECURITY_CONCERN = "security_concern"
    ROLLBACK_OCCURRED = "rollback_occurred"
    ENGINEERING_CYCLE_FAILED = "engineering_cycle_failed"


URGENT_EVENTS = frozenset(
    {
        NotificationEvent.APPROVAL_REQUIRED,
        NotificationEvent.BLOCKED,
        NotificationEvent.SECURITY_CONCERN,
        NotificationEvent.ROLLBACK_OCCURRED,
        NotificationEvent.ENGINEERING_CYCLE_FAILED,
        NotificationEvent.TEST_REGRESSION,
        NotificationEvent.BENCHMARK_REGRESSION,
    }
)


class NotificationRecord(StrictModel):
    """Delivery evidence without message content; content is hashed, never stored."""

    event: NotificationEvent
    channel: Identifier
    title: ShortText
    body_sha256: Sha256
    attempts: int = Field(ge=0)
    delivered: bool
    error_code: Identifier | None = None
    sent_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_delivery(self) -> NotificationRecord:
        if self.delivered and (self.error_code is not None or self.sent_at is None):
            raise ValueError("A delivered notification has a send time and no error")
        return self


class RollbackRecord(StrictModel):
    reverted_commit_sha: CommitSha
    revert_reference: ShortText
    reason: ShortText
    occurred_at: AwareDatetime
    attested_by: EngineerIssuer = "software_engineer.runtime"


class CycleRecord(StrictModel):
    """Runtime-attested observability and audit record of one engineering cycle."""

    schema_version: Literal[1] = 1
    contract_version: Literal["software-engineer-contract-v1"] = SOFTWARE_ENGINEER_CONTRACT_VERSION
    cycle_id: UUID
    agent_id: Literal["software_engineer.resident"] = SOFTWARE_ENGINEER_AGENT_ID
    mode: CycleMode
    model: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None
    repository_head: CommitSha
    started_at: AwareDatetime
    completed_at: AwareDatetime
    phase_reached: CyclePhase
    transitions: list[CycleTransition] = Field(min_length=1, max_length=100)
    signals: list[EngineeringSignal] = Field(default_factory=list, max_length=1000)
    candidates: list[EngineeringCandidate] = Field(default_factory=list, max_length=200)
    ranking: list[UUID] = Field(default_factory=list, max_length=200)
    selected_candidate_id: UUID | None = None
    abandoned_candidate_ids: list[UUID] = Field(default_factory=list, max_length=200)
    risk: RiskAssessment | None = None
    self_review: SelfReview | None = None
    gates: list[GateResult] = Field(default_factory=list, max_length=50)
    change: ChangeSummary | None = None
    decision: CycleDecision
    decision_reasons: list[ShortText] = Field(min_length=1, max_length=50)
    approval_request: ApprovalRequest | None = None
    owner_decision: OwnerDecision | None = None
    budget: CycleBudget
    usage: CycleUsage
    memory_reads: int = Field(default=0, ge=0)
    memory_writes: list[UUID] = Field(default_factory=list, max_length=100)
    notifications: list[NotificationRecord] = Field(default_factory=list, max_length=50)
    rollback: RollbackRecord | None = None
    failure: CycleFailure | None = None
    attested_by: EngineerIssuer = "software_engineer.runtime"

    @model_validator(mode="after")
    def validate_cycle(self) -> CycleRecord:
        if self.completed_at < self.started_at:
            raise ValueError("Cycle completion cannot precede start")
        sequences = [item.sequence for item in self.transitions]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("Cycle transitions must be contiguous")
        if self.transitions[0].source is not CyclePhase.CREATED:
            raise ValueError("Cycles start from created")
        if self.transitions[-1].target is not self.phase_reached:
            raise ValueError("Phase reached must be the last transition target")
        candidate_ids = {item.candidate_id for item in self.candidates}
        if len(candidate_ids) != len(self.candidates):
            raise ValueError("Candidate IDs must be unique")
        if set(self.ranking) != candidate_ids or len(self.ranking) != len(candidate_ids):
            raise ValueError("Ranking must order every candidate exactly once")
        if (
            self.selected_candidate_id is not None
            and self.selected_candidate_id not in candidate_ids
        ):
            raise ValueError("Selected candidate is unknown")
        if not set(self.abandoned_candidate_ids).issubset(candidate_ids):
            raise ValueError("Abandoned candidate is unknown")
        signal_ids = {item.signal_id for item in self.signals}
        if any(not set(item.signal_ids).issubset(signal_ids) for item in self.candidates):
            raise ValueError("Candidate cites a signal the cycle did not observe")
        if len({item.gate for item in self.gates}) != len(self.gates):
            raise ValueError("Gate results must be unique per gate")
        if self.usage.exceeded(self.budget) and self.failure is not CycleFailure.BUDGET_EXHAUSTED:
            raise ValueError("Usage beyond budget must be recorded as budget exhaustion")
        if self.decision is CycleDecision.SHIP:
            self._validate_ship()
        elif self.decision is CycleDecision.REQUEST_APPROVAL:
            if self.approval_request is None or self.selected_candidate_id is None:
                raise ValueError("Requesting approval requires a request and a candidate")
        elif self.decision is CycleDecision.NO_WORK:
            if self.selected_candidate_id is not None or self.change is not None:
                raise ValueError("A no-work cycle selects nothing and changes nothing")
        if self.owner_decision is not None and (
            self.approval_request is None
            or self.owner_decision.request_id != self.approval_request.request_id
        ):
            raise ValueError("Owner decision must answer this cycle's approval request")
        return self

    def _validate_ship(self) -> None:
        if self.mode is not CycleMode.AUTONOMOUS_LOW_RISK and self.owner_decision is None:
            raise ValueError("Shipping requires autonomous mode or an owner decision")
        if self.risk is None or self.change is None or self.self_review is None:
            raise ValueError("Shipping requires risk, change, and self-review evidence")
        if self.owner_decision is None:
            if self.risk.level is not RiskLevel.LOW:
                raise ValueError("Only low-risk changes ship without an owner decision")
            if self.self_review.blocking:
                raise ValueError("A blocking self-review cannot ship autonomously")
            if self.failure is not None:
                raise ValueError("A failed cycle cannot ship")
        elif self.owner_decision.verdict is not OwnerVerdict.SHIP:
            raise ValueError("Shipping after an owner decision requires a ship verdict")
        if any(item.status in {GateStatus.FAILED, GateStatus.ERROR} for item in self.gates):
            raise ValueError("A change with a failed gate cannot ship")


class CycleReport(StrictModel):
    """The concise daily report; it teaches the owner something about the project."""

    cycle_id: UUID
    report_date: date
    decision: CycleDecision
    inspected: list[ShortText] = Field(default_factory=list, max_length=50)
    discovered: list[ShortText] = Field(default_factory=list, max_length=50)
    fixed: list[ShortText] = Field(default_factory=list, max_length=20)
    why_it_mattered: list[ShortText] = Field(default_factory=list, max_length=20)
    validated: list[ShortText] = Field(default_factory=list, max_length=20)
    changed: list[ShortText] = Field(default_factory=list, max_length=50)
    learned: list[ShortText] = Field(default_factory=list, max_length=20)
    memory_added: list[ShortText] = Field(default_factory=list, max_length=20)
    unresolved: list[ShortText] = Field(default_factory=list, max_length=50)
    owner_action_required: bool
    text: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]


__all__ = [
    "CRITICAL_REVIEW_QUESTIONS",
    "SOFTWARE_ENGINEER_AGENT_ID",
    "SOFTWARE_ENGINEER_CONTRACT_VERSION",
    "SOFTWARE_ENGINEER_REVIEWER_ID",
    "URGENT_EVENTS",
    "ApprovalRequest",
    "CandidateEstimate",
    "ChangeCategory",
    "ChangeSummary",
    "CycleBudget",
    "CycleDecision",
    "CycleFailure",
    "CycleMode",
    "CyclePhase",
    "CycleRecord",
    "CycleReport",
    "CycleTransition",
    "CycleUsage",
    "EngineeringCandidate",
    "EngineeringSignal",
    "GateResult",
    "GateStatus",
    "NotificationEvent",
    "NotificationRecord",
    "OwnerDecision",
    "OwnerVerdict",
    "PublishedChange",
    "ReviewAnswer",
    "ReviewItem",
    "ReviewQuestion",
    "RiskAssessment",
    "RiskLevel",
    "RollbackRecord",
    "SelfReview",
    "SignalKind",
    "SignalSeverity",
    "ValidationGate",
    "category_risk",
    "highest",
]
