"""Owner-facing rendering: the approval request and the daily engineering report."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from nexus.software_engineer.models import (
    ApprovalRequest,
    CycleDecision,
    CycleRecord,
    CycleReport,
    GateStatus,
)


def render_approval_request(request: ApprovalRequest) -> str:
    validation = (
        "\n".join(
            f"- {item.gate.value}: {item.status.value} ({item.summary})"
            for item in request.validation
        )
        or "- no gates ran"
    )
    files = "\n".join(f"- {path}" for path in request.files_affected) or "- none"
    dry_run = " (dry run: no code was changed)" if request.dry_run else ""
    return (
        f"READY FOR REVIEW{dry_run}\n"
        f"\n"
        f"Title:\n{request.title}\n"
        f"\n"
        f"Problem:\n{request.problem}\n"
        f"\n"
        f"Root cause:\n{request.root_cause}\n"
        f"\n"
        f"Proposed fix:\n{request.proposed_fix}\n"
        f"\n"
        f"Why:\n{request.why}\n"
        f"\n"
        f"Files affected:\n{files}\n"
        f"\n"
        f"Behavior changed:\n{request.behavior_changed}\n"
        f"\n"
        f"Risk:\n{request.risk.level.value.upper()} ({'; '.join(request.risk.reasons[:3])})\n"
        f"\n"
        f"Validation:\n{validation}\n"
        f"\n"
        f"Benchmark impact:\n{request.benchmark_impact}\n"
        f"\n"
        f"Security impact:\n{request.security_impact}\n"
        f"\n"
        f"Agent confidence:\n{request.confidence}%\n"
        f"\n"
        f"Rollback plan:\n{request.rollback_plan}\n"
        f"\n"
        f"Reference:\n{request.reference}\n"
        f"\n"
        f"Recommendation:\n{request.recommendation.upper()}\n"
        f"\n"
        f"Owner decision requested:\nSHIP / REVISE / REJECT\n"
        f"\n"
        f"Question:\n{request.question}\n"
    )


def build_report(
    record: CycleRecord,
    *,
    learned: Sequence[str] = (),
    memory_added: Sequence[str] = (),
    now: datetime,
) -> CycleReport:
    """Summarize one cycle for the owner from runtime evidence only."""

    kinds: dict[str, int] = {}
    for signal in record.signals:
        kinds[signal.kind.value] = kinds.get(signal.kind.value, 0) + 1
    inspected = [f"{kind}: {count} signal(s)" for kind, count in sorted(kinds.items())]
    by_id = {item.candidate_id: item for item in record.candidates}
    discovered = [
        f"{by_id[candidate_id].title} [{by_id[candidate_id].category.value}]"
        for candidate_id in record.ranking
    ][:20]
    fixed: list[str] = []
    why: list[str] = []
    selected = by_id.get(record.selected_candidate_id) if record.selected_candidate_id else None
    if record.decision is CycleDecision.SHIP and selected is not None:
        fixed.append(selected.title)
        why.append(selected.rationale[:500])
    validated = [f"{item.gate.value}: {item.status.value}" for item in record.gates]
    changed = list(record.change.changed_files) if record.change is not None else []
    unresolved = [
        f"{by_id[candidate_id].title}"
        for candidate_id in record.ranking
        if candidate_id != record.selected_candidate_id
    ][:20]
    owner_action = record.decision in {CycleDecision.REQUEST_APPROVAL, CycleDecision.BLOCKED} or (
        record.failure is not None
    )
    headline = {
        CycleDecision.NO_WORK: "No sufficiently valuable safe work found today.",
        CycleDecision.SHIP: "Shipped one low-risk change after every gate passed.",
        CycleDecision.REQUEST_APPROVAL: "One change is ready for your decision.",
        CycleDecision.ABANDON: "Attempted one change and abandoned it on evidence.",
        CycleDecision.BLOCKED: "The cycle is blocked and needs you.",
    }[record.decision]
    lines = [
        f"# Resident engineer report - {now.date().isoformat()}",
        "",
        headline,
        "",
        "## Inspected",
        *(f"- {item}" for item in inspected or ["- nothing observable"]),
        "",
        "## Discovered",
        *(f"- {item}" for item in discovered or ["nothing worth changing"]),
    ]
    if fixed:
        lines += ["", "## Fixed", *(f"- {item}" for item in fixed), "", "## Why it mattered"]
        lines += [f"- {item}" for item in why]
    if validated:
        lines += ["", "## Validated", *(f"- {item}" for item in validated)]
    if changed:
        lines += ["", "## Changed", *(f"- {item}" for item in changed)]
    if record.decision is CycleDecision.REQUEST_APPROVAL and record.approval_request is not None:
        lines += [
            "",
            "## Owner action required",
            f"- {record.approval_request.title}: SHIP / REVISE / REJECT",
        ]
    if learned:
        lines += ["", "## Learned", *(f"- {item}" for item in learned)]
    if memory_added:
        lines += ["", "## Added to memory", *(f"- {item}" for item in memory_added)]
    if unresolved:
        lines += ["", "## Still open", *(f"- {item}" for item in unresolved)]
    failed_gates = [item.gate.value for item in record.gates if item.status is GateStatus.FAILED]
    if failed_gates:
        lines += ["", "## Failed gates", *(f"- {item}" for item in failed_gates)]
    if record.failure is not None:
        lines += ["", f"## Cycle failure: {record.failure.value}"]
    lines += [
        "",
        f"Decision: {record.decision.value}. Reasons: {'; '.join(record.decision_reasons[:3])}.",
        f"Budget: {record.usage.turns}/{record.budget.max_turns} turns, "
        f"{record.usage.tool_calls}/{record.budget.max_tool_calls} tool calls, "
        f"{record.usage.model_calls}/{record.budget.max_model_calls} model calls, "
        f"{record.usage.input_tokens + record.usage.output_tokens} tokens, "
        + (
            f"${record.usage.cost_usd:.4f}"
            if record.usage.cost_usd is not None
            else "cost not measured"
        )
        + (
            f"/${record.budget.max_cost_usd:.2f}."
            if record.budget.max_cost_usd is not None
            else "."
        ),
    ]
    text = "\n".join(lines)[:20_000]
    return CycleReport(
        cycle_id=record.cycle_id,
        report_date=now.date(),
        decision=record.decision,
        inspected=[item[:500] for item in inspected][:50],
        discovered=[item[:500] for item in discovered][:50],
        fixed=[item[:500] for item in fixed][:20],
        why_it_mattered=[item[:500] for item in why][:20],
        validated=[item[:500] for item in validated][:20],
        changed=[item[:500] for item in changed][:50],
        learned=[item[:500] for item in learned][:20],
        memory_added=[item[:500] for item in memory_added][:20],
        unresolved=[item[:500] for item in unresolved][:50],
        owner_action_required=owner_action,
        text=text,
    )


__all__ = ["build_report", "render_approval_request"]
