"""Sanitized view models: the complete contract between NEXUS and the browser.

These are built field by field from validated NEXUS records by ``views``; none is a dump of
a record or of settings. Text is bounded and credential-checked by ``sanitize`` before it
reaches a model here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Literal[1] = 1

Provenance = Literal["live", "recorded", "replay", "static"]
SourceState = Literal["ok", "absent", "unreadable", "unavailable", "disabled", "preparing"]
Actor = Literal["nexus", "aegisops", "patchforge", "sentinelqa", "resident_engineer", "memory"]
SystemId = Literal[
    "nexus", "aegisops", "patchforge", "sentinelqa", "resident_engineer", "memory", "engram"
]
SystemStatus = Literal["idle", "dormant", "active", "pipeline", "attention", "offline", "not_built"]
"""``pipeline``: part of the executor stage now running, where which member is working
at this instant is not observable (PatchForge, then SentinelQA, inside one call)."""
GateOwner = Literal["patchforge", "sentinelqa", "repository"]
Tone = Literal["neutral", "good", "warn", "bad"]


class ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceStatus(ViewModel):
    source: str
    label: str
    state: SourceState
    provenance: Provenance
    detail: str | None = None
    observed_at: datetime | None = None


class Fact(ViewModel):
    label: str
    value: str
    provenance: Provenance
    tone: Tone = "neutral"


# -- cycle ------------------------------------------------------------------------------


class TransitionView(ViewModel):
    sequence: int
    source: str
    target: str
    stage: str
    reason: str
    occurred_at: datetime
    actors: list[Actor]
    """Systems this step involved, in order, as evidenced by the record itself."""

    evidence_note: str | None = None


class GateView(ViewModel):
    gate: str
    owner: GateOwner
    status: Literal["passed", "failed", "error", "not_run"]
    summary: str
    evidence_sha256: str | None = None


class RiskView(ViewModel):
    level: Literal["low", "medium", "high"]
    category_level: str
    path_level: str
    size_level: str
    uncertain: bool
    governing_paths: list[str]
    reasons: list[str]


class ReviewItemView(ViewModel):
    question: str
    answer: Literal["clear", "concern", "unknown"]
    note: str


class SelfReviewView(ViewModel):
    items: list[ReviewItemView]
    blocking: bool
    requires_human: bool
    reviewed_diff_sha256: str | None = None


class SignalView(ViewModel):
    kind: str
    severity: Literal["info", "warning", "failure"]
    source: str
    summary: str
    untrusted_text: bool
    instruction_like: int
    observed_at: datetime


class CandidateView(ViewModel):
    candidate_id: str
    title: str
    category: str
    category_risk: Literal["low", "medium", "high"]
    expected_paths: list[str]
    score: int
    rank: int
    blockers: list[str]
    derived_from_untrusted_text: bool
    selected: bool
    abandoned: bool


class PublicationView(ViewModel):
    authority: str
    repository: str
    base_branch: str
    branch: str
    pull_request_number: int
    pull_request_url: str
    draft: bool
    published_by: str
    published_at: datetime


class ChangeView(ViewModel):
    base_sha: str
    branch: str | None
    commit_sha: str | None
    changed_files: list[str]
    additions: int
    deletions: int
    diff_bytes: int
    diff_sha256: str
    patch_result_sha256: str | None
    sentinel_verdict_sha256: str | None
    rollback_reference: str
    publication: PublicationView | None


class DecisionView(ViewModel):
    decision_id: str
    request_id: str
    verdict: Literal["ship", "revise", "reject"]
    decided_by: str
    reason: str
    channel: str
    decided_at: datetime


class ApprovalView(ViewModel):
    request_id: str
    cycle_id: str
    title: str
    question: str
    problem: str
    root_cause: str
    proposed_fix: str
    why: str
    behavior_changed: str
    files_affected: list[str]
    validation: list[GateView]
    benchmark_impact: str
    security_impact: str
    risk: RiskView
    confidence: int
    rollback_plan: str
    recommendation: Literal["ship", "revise", "reject"]
    dry_run: bool
    created_at: datetime
    status: Literal["pending", "decided"]
    decision: DecisionView | None
    governed_channels: list[str]
    """The only ways to answer: the signed Slack command and the owner CLI. The Command
    Center shows them; it never records a decision."""


class NotificationView(ViewModel):
    event: str
    channel: str
    title: str
    delivered: bool
    attempts: int
    error_code: str | None
    sent_at: datetime | None


class BudgetLine(ViewModel):
    dimension: str
    used: float
    limit: float | None


class SentinelFindingView(ViewModel):
    code: str
    category: str
    severity: str
    detail: str
    path: str | None


class SentinelVerdictView(ViewModel):
    verdict: str
    summary: str
    findings: list[SentinelFindingView]
    specification_runs: int
    patchforge_checks_agree: bool | None
    lock_sha256: str
    completed_at: datetime


class CycleSummary(ViewModel):
    cycle_id: str
    mode: str
    started_at: datetime
    completed_at: datetime
    phase_reached: str
    decision: str
    failure: str | None
    risk_level: str | None
    selected_title: str | None
    gates_passed: int
    gates_failed: int
    owner_action_required: bool
    published: bool


class CycleView(CycleSummary):
    repository_head: str
    model: str | None
    transitions: list[TransitionView]
    signals: list[SignalView]
    candidates: list[CandidateView]
    risk: RiskView | None
    self_review: SelfReviewView | None
    gates: list[GateView]
    change: ChangeView | None
    decision_reasons: list[str]
    approval: ApprovalView | None
    budget: list[BudgetLine]
    memory_reads: int
    memory_writes: int
    notifications: list[NotificationView]
    rollback_reason: str | None
    sentinel: SentinelVerdictView | None
    report_text: str | None


# -- live state -------------------------------------------------------------------------


class ActiveRunView(ViewModel):
    cycle_id: str
    started_at: datetime
    lease_expires_at: datetime
    phase: str | None = None
    phase_source: Literal["unobservable", "progress_file"] = "unobservable"
    sequence: int | None = None
    stage: str | None = None
    description: str | None = None
    phase_updated_at: datetime | None = None
    executor: Literal["dry_run", "patchforge", "other"] | None = None
    actors: list[Actor] = Field(default_factory=list)
    """Systems the runtime's own code path involves in this phase, fully active."""

    pipeline: list[Actor] = Field(default_factory=list)
    """Systems running inside the executor call, one after another, not individually
    observable."""


class InterruptedRunView(ViewModel):
    cycle_id: str
    started_at: datetime | None
    reason: str
    recovered_at: datetime | None


class MemoryEntryView(ViewModel):
    memory_id: str
    category: str
    status: str
    lifecycle: str
    confidence: int
    tags: list[str]
    created_at: datetime
    cycle_id: str
    content: str


class EngineerMemoryView(ViewModel):
    state: SourceState
    total: int = 0
    by_category: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    by_lifecycle: dict[str, int] = Field(default_factory=dict)
    recent: list[MemoryEntryView] = Field(default_factory=list)


class BrainMemoryView(ViewModel):
    state: SourceState
    total: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    by_state: dict[str, int] = Field(default_factory=dict)


class MemoryView(ViewModel):
    engineer: EngineerMemoryView
    aegisops_brain: BrainMemoryView


class ServiceHealthView(ViewModel):
    service: str
    status: Literal["healthy", "unhealthy", "unavailable", "unknown"]
    dependencies: dict[str, str]


class HealthView(ViewModel):
    state: SourceState
    overall: Literal["healthy", "degraded", "unavailable", "unknown"]
    services: list[ServiceHealthView]
    observed_at: datetime | None


class LabScenarioView(ViewModel):
    """Only operator-facing identity; ground truth never leaves the server."""

    id: str
    title: str
    target_service: str


class EngineerConfigView(ViewModel):
    enabled: bool
    mode: str
    sandbox: str
    model_configured: bool
    model_recipes_allowed: bool
    publish_from_cycle: bool
    read_issues: bool
    slack_webhook_present: bool
    slack_owner_configured: bool
    github_token_present: bool
    slack_channel_label: str
    schedule_cron_intent: str
    budget: list[BudgetLine]


class SystemView(ViewModel):
    id: SystemId
    name: str
    designation: str
    role: str
    status: SystemStatus
    status_detail: str
    last_activity_at: datetime | None
    facts: list[Fact]


class Snapshot(ViewModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    mode: Literal["live"] = "live"
    version: str
    sequence: int
    generated_at: datetime
    sources: list[SourceStatus]
    systems: list[SystemView]
    active_run: ActiveRunView | None
    interrupted: list[InterruptedRunView]
    latest_cycle: CycleView | None
    cycles: list[CycleSummary]
    pending_approval: ApprovalView | None
    decisions: list[DecisionView]
    publications: list[PublicationView]
    memory: MemoryView
    health: HealthView
    lab_scenarios: list[LabScenarioView]
    engineer_config: EngineerConfigView | None
    required_autonomous_gates: list[str]
    """The ship policy's gates: code, not configuration, so always visible."""
    """``None`` when this deployment cannot see the engineer's configuration."""


# -- replay -----------------------------------------------------------------------------


class OwnerStepView(ViewModel):
    verdict: Literal["ship", "revise", "reject"]
    policy_outcome: str
    note: str


class ReplayEpisodeSummary(ViewModel):
    name: str
    title: str
    synopsis: str
    decision: str
    risk_level: str | None
    record_sha256: str


class ReplayEpisode(ReplayEpisodeSummary):
    provenance: str
    tempo_note: str
    scripted_systems: list[SystemId]
    """Systems a scripted stand-in played in this episode; the client marks them."""

    cycle: CycleView
    owner_step: OwnerStepView | None


class ReplayCatalog(ViewModel):
    state: SourceState
    detail: str | None
    episodes: list[ReplayEpisodeSummary]


class Signal(ViewModel):
    """A semantic change between two snapshots, for the animation layer."""

    kind: Literal[
        "run.started",
        "run.phase",
        "run.ended",
        "cycle.recorded",
        "decision.pending",
        "decision.recorded",
        "publication.recorded",
        "health.changed",
    ]
    subject: str
    detail: str | None = None


__all__ = [
    "SCHEMA_VERSION",
    "ActiveRunView",
    "Actor",
    "ApprovalView",
    "BrainMemoryView",
    "BudgetLine",
    "CandidateView",
    "ChangeView",
    "CycleSummary",
    "CycleView",
    "DecisionView",
    "EngineerConfigView",
    "EngineerMemoryView",
    "Fact",
    "GateView",
    "HealthView",
    "InterruptedRunView",
    "LabScenarioView",
    "MemoryEntryView",
    "MemoryView",
    "NotificationView",
    "OwnerStepView",
    "Provenance",
    "PublicationView",
    "ReplayCatalog",
    "ReplayEpisode",
    "ReplayEpisodeSummary",
    "ReviewItemView",
    "RiskView",
    "SelfReviewView",
    "SentinelFindingView",
    "SentinelVerdictView",
    "ServiceHealthView",
    "Signal",
    "SignalView",
    "Snapshot",
    "SourceState",
    "SourceStatus",
    "SystemId",
    "SystemStatus",
    "SystemView",
    "Tone",
    "TransitionView",
    "ViewModel",
]
