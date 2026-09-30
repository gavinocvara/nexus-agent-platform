"""Build sanitized view models from validated NEXUS records.

Semantics live here, not in the browser: which system a cycle step involved is decided from
the record's own evidence (a PatchForge gate that ran, a SentinelQA verdict, a memory write),
so the client animates only what the record proves.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from uuid import UUID

from nexus.command_center.models import (
    Actor,
    ApprovalView,
    BudgetLine,
    CandidateView,
    ChangeView,
    CycleSummary,
    CycleView,
    DecisionView,
    GateOwner,
    GateView,
    NotificationView,
    PublicationView,
    ReviewItemView,
    RiskView,
    SelfReviewView,
    SentinelFindingView,
    SentinelVerdictView,
    SignalView,
    TransitionView,
)
from nexus.command_center.sanitize import safe_list, safe_optional, safe_text
from nexus.sentinelqa.models import SentinelVerdict
from nexus.software_engineer.models import (
    ApprovalRequest,
    CycleBudget,
    CyclePhase,
    CycleRecord,
    CycleUsage,
    GateResult,
    GateStatus,
    OwnerDecision,
    PublishedChange,
    RiskAssessment,
    ValidationGate,
    category_risk,
)

STAGE: dict[CyclePhase, str] = {
    CyclePhase.CREATED: "CREATED",
    CyclePhase.OBSERVE: "OBSERVE",
    CyclePhase.UNDERSTAND: "UNDERSTAND",
    CyclePhase.PRIORITIZE: "PRIORITIZE",
    CyclePhase.INVESTIGATE: "INVESTIGATE",
    CyclePhase.PLAN: "PLAN",
    CyclePhase.IMPLEMENT: "IMPLEMENT",
    CyclePhase.TEST: "TEST",
    CyclePhase.SELF_REVIEW: "REVIEW",
    CyclePhase.ASSESS_RISK: "RISK",
    CyclePhase.DECIDE: "DECIDE",
    CyclePhase.OBSERVE_RESULTS: "VERIFY",
    CyclePhase.LEARN: "LEARN",
    CyclePhase.REPORT: "REPORT",
    CyclePhase.CLOSED: "CLOSED",
}

PATCHFORGE_GATES = frozenset(
    {
        ValidationGate.RUFF_FORMAT,
        ValidationGate.RUFF_LINT,
        ValidationGate.MYPY,
        ValidationGate.PYTEST_TARGETED,
        ValidationGate.PYTEST_FULL,
    }
)
SENTINEL_GATES = frozenset({ValidationGate.SENTINEL_GATE, ValidationGate.SENTINEL_REVIEW})


def gate_owner(gate: ValidationGate) -> GateOwner:
    if gate in PATCHFORGE_GATES:
        return "patchforge"
    if gate in SENTINEL_GATES:
        return "sentinelqa"
    return "repository"


def _ran(gate: GateResult) -> bool:
    return gate.status is not GateStatus.NOT_RUN


def patchforge_ran(record: CycleRecord) -> bool:
    return record.change is not None or any(
        _ran(item) for item in record.gates if item.gate in PATCHFORGE_GATES
    )


def sentinel_ran(record: CycleRecord) -> bool:
    return (record.change is not None and record.change.sentinel_verdict_sha256 is not None) or any(
        _ran(item) for item in record.gates if item.gate in SENTINEL_GATES
    )


def transition_views(record: CycleRecord) -> list[TransitionView]:
    forged = patchforge_ran(record)
    verified = sentinel_ran(record)
    views: list[TransitionView] = []
    for item in record.transitions:
        actors: list[Actor]
        note: str | None = None
        target = item.target
        if target is CyclePhase.UNDERSTAND:
            actors = ["memory"] if record.memory_reads else ["resident_engineer"]
        elif target is CyclePhase.IMPLEMENT:
            actors = ["patchforge"] if forged else ["resident_engineer"]
            if not forged:
                note = "No executor produced a change in this cycle."
        elif target is CyclePhase.TEST:
            actors = []
            if forged:
                actors.append("patchforge")
            if verified:
                actors.append("sentinelqa")
            if not actors:
                actors = ["resident_engineer"]
                note = "No validation gate ran in this cycle."
        elif target in {CyclePhase.ASSESS_RISK, CyclePhase.DECIDE, CyclePhase.REPORT}:
            actors = ["nexus"]
        elif target is CyclePhase.CLOSED:
            actors = ["nexus"]
        elif target is CyclePhase.LEARN:
            actors = ["memory"] if record.memory_writes else ["resident_engineer"]
        else:
            actors = ["resident_engineer"]
        views.append(
            TransitionView(
                sequence=item.sequence,
                source=item.source.value,
                target=target.value,
                stage=STAGE[target],
                reason=safe_text(item.reason),
                occurred_at=item.occurred_at,
                actors=actors,
                evidence_note=note,
            )
        )
    return views


def gate_views(gates: Sequence[GateResult]) -> list[GateView]:
    return [
        GateView(
            gate=item.gate.value,
            owner=gate_owner(item.gate),
            status=item.status.value,
            summary=safe_text(item.summary),
            evidence_sha256=item.evidence_sha256,
        )
        for item in gates
    ]


def risk_view(risk: RiskAssessment) -> RiskView:
    return RiskView(
        level=risk.level.value,
        category_level=risk.category_level.value,
        path_level=risk.path_level.value,
        size_level=risk.size_level.value,
        uncertain=risk.uncertain,
        governing_paths=safe_list(list(risk.governing_paths), 300),
        reasons=safe_list(list(risk.reasons)),
    )


def decision_view(decision: OwnerDecision) -> DecisionView:
    return DecisionView(
        decision_id=str(decision.decision_id),
        request_id=str(decision.request_id),
        verdict=decision.verdict.value,
        decided_by=f"{decision.decided_by.actor_type.value}:{decision.decided_by.actor_id}",
        reason=safe_text(decision.reason),
        channel=decision.channel,
        decided_at=decision.decided_at,
    )


def publication_view(publication: PublishedChange) -> PublicationView:
    return PublicationView(
        authority=publication.authority,
        repository=safe_text(publication.repository, 200),
        base_branch=safe_text(publication.base_branch, 255),
        branch=safe_text(publication.branch, 255),
        pull_request_number=publication.pull_request_number,
        pull_request_url=safe_text(publication.pull_request_url),
        draft=publication.draft,
        published_by=(
            f"{publication.published_by.actor_type.value}:{publication.published_by.actor_id}"
        ),
        published_at=publication.published_at,
    )


def governed_channels(cycle_id: UUID) -> list[str]:
    return [
        f"Slack  /nexus ship|revise|reject {cycle_id} <reason>",
        (
            f"CLI    python -m nexus.software_engineer decide --cycle {cycle_id} "
            '--verdict ship|revise|reject --reason "<why>"'
        ),
    ]


def approval_view(request: ApprovalRequest, decision: OwnerDecision | None) -> ApprovalView:
    return ApprovalView(
        request_id=str(request.request_id),
        cycle_id=str(request.cycle_id),
        title=safe_text(request.title),
        question=safe_text(request.question),
        problem=safe_text(request.problem, 1500),
        root_cause=safe_text(request.root_cause, 1500),
        proposed_fix=safe_text(request.proposed_fix, 1500),
        why=safe_text(request.why, 1500),
        behavior_changed=safe_text(request.behavior_changed, 1500),
        files_affected=safe_list(list(request.files_affected), 300, 60),
        validation=gate_views(request.validation),
        benchmark_impact=safe_text(request.benchmark_impact),
        security_impact=safe_text(request.security_impact),
        risk=risk_view(request.risk),
        confidence=request.confidence,
        rollback_plan=safe_text(request.rollback_plan, 1500),
        recommendation=request.recommendation,
        dry_run=request.dry_run,
        created_at=request.created_at,
        status="pending" if decision is None else "decided",
        decision=None if decision is None else decision_view(decision),
        governed_channels=governed_channels(request.cycle_id),
    )


def budget_lines(budget: CycleBudget, usage: CycleUsage) -> list[BudgetLine]:
    pairs: list[tuple[str, float, float | None]] = [
        ("runtime_seconds", usage.runtime_seconds, budget.max_runtime_seconds),
        ("turns", usage.turns, budget.max_turns),
        ("tool_calls", usage.tool_calls, budget.max_tool_calls),
        ("model_calls", usage.model_calls, budget.max_model_calls),
        ("input_tokens", usage.input_tokens, budget.max_input_tokens),
        ("output_tokens", usage.output_tokens, budget.max_output_tokens),
        ("cost_usd", usage.cost_usd or 0.0, budget.max_cost_usd),
        ("changed_files", usage.changed_files, budget.max_changed_files),
        ("diff_bytes", usage.diff_bytes, budget.max_diff_bytes),
    ]
    return [BudgetLine(dimension=name, used=used, limit=limit) for name, used, limit in pairs]


def sentinel_view(verdict: SentinelVerdict) -> SentinelVerdictView:
    return SentinelVerdictView(
        verdict=verdict.verdict.value,
        summary=safe_text(verdict.summary, 1000),
        findings=[
            SentinelFindingView(
                code=item.code.value,
                category=item.category.value,
                severity=item.severity.value,
                detail=safe_text(item.detail),
                path=safe_optional(item.path, 300),
            )
            for item in verdict.findings[:50]
        ],
        specification_runs=len(verdict.runs),
        patchforge_checks_agree=verdict.patchforge_checks_agree,
        lock_sha256=verdict.lock_sha256,
        completed_at=verdict.completed_at,
    )


def effective_decision(record: CycleRecord, stored: OwnerDecision | None) -> OwnerDecision | None:
    return record.owner_decision if record.owner_decision is not None else stored


def effective_publication(
    record: CycleRecord, stored: PublishedChange | None
) -> PublishedChange | None:
    if record.change is not None and record.change.publication is not None:
        return record.change.publication
    return stored


def cycle_summary(
    record: CycleRecord,
    *,
    decision: OwnerDecision | None = None,
    publication: PublishedChange | None = None,
) -> CycleSummary:
    selected = _selected_title(record)
    owner = effective_decision(record, decision)
    return CycleSummary(
        cycle_id=str(record.cycle_id),
        mode=record.mode.value,
        started_at=record.started_at,
        completed_at=record.completed_at,
        phase_reached=record.phase_reached.value,
        decision=record.decision.value,
        failure=None if record.failure is None else record.failure.value,
        risk_level=None if record.risk is None else record.risk.level.value,
        selected_title=selected,
        gates_passed=sum(1 for item in record.gates if item.status is GateStatus.PASSED),
        gates_failed=sum(
            1 for item in record.gates if item.status in {GateStatus.FAILED, GateStatus.ERROR}
        ),
        owner_action_required=record.approval_request is not None and owner is None,
        published=effective_publication(record, publication) is not None,
    )


def cycle_view(
    record: CycleRecord,
    *,
    decision: OwnerDecision | None = None,
    publication: PublishedChange | None = None,
    sentinel: SentinelVerdict | None = None,
    report_text: str | None = None,
) -> CycleView:
    summary = cycle_summary(record, decision=decision, publication=publication)
    owner = effective_decision(record, decision)
    published = effective_publication(record, publication)
    ranks = {candidate_id: index for index, candidate_id in enumerate(record.ranking, 1)}
    abandoned = set(record.abandoned_candidate_ids)
    change: ChangeView | None = None
    if record.change is not None:
        item = record.change
        change = ChangeView(
            base_sha=item.base_sha,
            branch=safe_optional(item.branch, 255),
            commit_sha=item.commit_sha,
            changed_files=safe_list(list(item.changed_files), 300, 100),
            additions=item.additions,
            deletions=item.deletions,
            diff_bytes=item.diff_bytes,
            diff_sha256=item.diff_sha256,
            patch_result_sha256=item.patch_result_sha256,
            sentinel_verdict_sha256=item.sentinel_verdict_sha256,
            rollback_reference=safe_text(item.rollback_reference),
            publication=None if published is None else publication_view(published),
        )
    review = record.self_review
    return CycleView(
        **summary.model_dump(),
        repository_head=record.repository_head,
        model=safe_optional(record.model, 100),
        transitions=transition_views(record),
        signals=[
            SignalView(
                kind=item.kind.value,
                severity=item.severity.value,
                source=safe_text(item.source, 200),
                summary=safe_text(item.summary),
                untrusted_text=item.untrusted_text,
                instruction_like=len(item.instruction_like),
                observed_at=item.observed_at,
            )
            for item in record.signals[:60]
        ],
        candidates=[
            CandidateView(
                candidate_id=str(item.candidate_id),
                title=safe_text(item.title),
                category=item.category.value,
                category_risk=category_risk(item.category).value,
                expected_paths=safe_list(list(item.expected_paths), 300, 20),
                score=item.estimate.score,
                rank=ranks.get(item.candidate_id, 0),
                blockers=safe_list(list(item.blockers)),
                derived_from_untrusted_text=item.derived_from_untrusted_text,
                selected=item.candidate_id == record.selected_candidate_id,
                abandoned=item.candidate_id in abandoned,
            )
            for item in sorted(record.candidates, key=lambda c: ranks.get(c.candidate_id, 0))[:30]
        ],
        risk=None if record.risk is None else risk_view(record.risk),
        self_review=(
            None
            if review is None
            else SelfReviewView(
                items=[
                    ReviewItemView(
                        question=item.question.value,
                        answer=item.answer.value,
                        note=safe_text(item.note),
                    )
                    for item in review.items
                ],
                blocking=review.blocking,
                requires_human=review.requires_human,
                reviewed_diff_sha256=review.reviewed_diff_sha256,
            )
        ),
        gates=gate_views(record.gates),
        change=change,
        decision_reasons=safe_list(list(record.decision_reasons)),
        approval=(
            None
            if record.approval_request is None
            else approval_view(record.approval_request, owner)
        ),
        budget=budget_lines(record.budget, record.usage),
        memory_reads=record.memory_reads,
        memory_writes=len(record.memory_writes),
        notifications=[
            NotificationView(
                event=item.event.value,
                channel=safe_text(item.channel, 100),
                title=safe_text(item.title),
                delivered=item.delivered,
                attempts=item.attempts,
                error_code=safe_optional(item.error_code, 100),
                sent_at=item.sent_at,
            )
            for item in record.notifications
        ],
        rollback_reason=None if record.rollback is None else safe_text(record.rollback.reason),
        sentinel=None if sentinel is None else sentinel_view(sentinel),
        report_text=safe_optional(report_text, 12_000),
    )


def _selected_title(record: CycleRecord) -> str | None:
    for item in record.candidates:
        if item.candidate_id == record.selected_candidate_id:
            return safe_text(item.title)
    return None


def counter_dict(values: Sequence[tuple[str, int]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for key, count in values:
        counts[key] += count
    return dict(sorted(counts.items()))


__all__ = [
    "PATCHFORGE_GATES",
    "SENTINEL_GATES",
    "STAGE",
    "approval_view",
    "budget_lines",
    "counter_dict",
    "cycle_summary",
    "cycle_view",
    "decision_view",
    "effective_decision",
    "effective_publication",
    "gate_owner",
    "gate_views",
    "governed_channels",
    "patchforge_ran",
    "publication_view",
    "risk_view",
    "sentinel_ran",
    "sentinel_view",
    "transition_views",
]
