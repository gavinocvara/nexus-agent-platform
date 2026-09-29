"""The closed daily engineering cycle.

``EngineeringCycle.run`` walks a fixed phase machine: observe, understand, prioritize,
investigate, plan, implement, test, self-review, assess risk, decide, observe results,
learn, report. The runtime owns every transition, budget, gate result, decision, memory
write, and notification. Producing a change is delegated to a ``CandidateExecutor``; the
default ``DryRunExecutor`` never changes code, and ``dry_run`` mode never uses anything
else. "Nothing worth changing today" is a successful outcome.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from nexus.patchforge.canonical import canonical_json
from nexus.software_engineer.config import SoftwareEngineerSettings
from nexus.software_engineer.inspect import CandidateGenerator, RepositoryInspector
from nexus.software_engineer.memory import (
    EngineerMemory,
    EngineerMemoryStore,
    EpistemicStatus,
    MemoryCategory,
    MemoryQuery,
    MemorySource,
    MemoryStoreError,
    observation,
)
from nexus.software_engineer.models import (
    ApprovalRequest,
    ChangeSummary,
    CycleBudget,
    CycleDecision,
    CycleFailure,
    CycleMode,
    CyclePhase,
    CycleRecord,
    CycleReport,
    CycleTransition,
    CycleUsage,
    EngineeringCandidate,
    EngineeringSignal,
    GateResult,
    GateStatus,
    NotificationEvent,
    OwnerDecision,
    ReviewQuestion,
    RiskAssessment,
    RiskLevel,
    RollbackRecord,
    SelfReview,
    SignalKind,
    SignalSeverity,
)
from nexus.software_engineer.notify import Notification, Notifier
from nexus.software_engineer.policy import (
    REQUIRED_GATES_FOR_AUTONOMOUS_SHIP,
    PolicyDecision,
    ShipPolicy,
)
from nexus.software_engineer.report import build_report, render_approval_request
from nexus.software_engineer.review import DiffFacts, SelfReviewer, diff_facts
from nexus.software_engineer.risk import classify_change
from nexus.software_engineer.trust import OwnerCommand, authorize_owner_command

_CYCLE_NAMESPACE = UUID("e3f4a5b6-c7d8-4e9f-a0b1-c2d3e4f5a6b7")
_ORDER = list(CyclePhase)


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """What an executor produced; every field is runtime evidence, never narrative."""

    change: ChangeSummary | None
    patch: bytes | None
    gates: list[GateResult]
    notes: tuple[str, ...] = ()
    root_cause_evidence: bool = False
    tool_calls: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class CandidateExecutor(Protocol):
    """Produces, ships, and rolls back a change for one candidate."""

    @property
    def can_ship(self) -> bool:
        """Whether a publisher exists; without one every validated change is proposed."""
        ...

    def execute(
        self, candidate: EngineeringCandidate, *, cycle_id: UUID, budget: CycleBudget
    ) -> ExecutionOutcome: ...

    def ship(self, change: ChangeSummary, *, cycle_id: UUID) -> ChangeSummary: ...

    def rollback(self, change: ChangeSummary, *, reason: str) -> RollbackRecord: ...


class ExecutorError(RuntimeError):
    """The executor could not produce, ship, or roll back a change."""


class DryRunExecutor:
    """Plans only. It never touches the tree, so it can never ship or roll back."""

    can_ship = False

    def execute(
        self, candidate: EngineeringCandidate, *, cycle_id: UUID, budget: CycleBudget
    ) -> ExecutionOutcome:
        gates = [
            GateResult(gate=gate, status=GateStatus.NOT_RUN, summary="dry run: not executed")
            for gate in sorted(REQUIRED_GATES_FOR_AUTONOMOUS_SHIP, key=lambda item: item.value)
        ]
        return ExecutionOutcome(change=None, patch=None, gates=gates, notes=("dry run",))

    def ship(self, change: ChangeSummary, *, cycle_id: UUID) -> ChangeSummary:
        raise ExecutorError("A dry run cannot ship")

    def rollback(self, change: ChangeSummary, *, reason: str) -> RollbackRecord:
        raise ExecutorError("A dry run has nothing to roll back")


class _BudgetExhausted(Exception):
    def __init__(self, dimensions: list[str]) -> None:
        super().__init__(", ".join(dimensions))
        self.dimensions = dimensions


@dataclass(slots=True)
class _State:
    signals: list[EngineeringSignal] = field(default_factory=list)
    memories: list[EngineerMemory] = field(default_factory=list)
    candidates: list[EngineeringCandidate] = field(default_factory=list)
    selected: EngineeringCandidate | None = None
    abandoned: list[UUID] = field(default_factory=list)
    outcome: ExecutionOutcome | None = None
    facts: DiffFacts | None = None
    review: SelfReview | None = None
    risk: RiskAssessment | None = None
    policy: PolicyDecision | None = None
    request: ApprovalRequest | None = None
    rollback: RollbackRecord | None = None
    failure: CycleFailure | None = None
    memory_writes: list[UUID] = field(default_factory=list)
    memory_summaries: list[str] = field(default_factory=list)
    learned: list[str] = field(default_factory=list)
    memory_reads: int = 0
    decision_reasons: list[str] = field(default_factory=list)


class EngineeringCycle:
    """One bounded, auditable pass over the repository."""

    def __init__(
        self,
        *,
        settings: SoftwareEngineerSettings,
        inspector: RepositoryInspector,
        memory: EngineerMemoryStore,
        notifier: Notifier,
        executor: CandidateExecutor | None = None,
        generator: CandidateGenerator | None = None,
        reviewer: SelfReviewer | None = None,
        clock: Callable[[], datetime],
        cycle_id: UUID | None = None,
        since: str | None = None,
        state_root: Path | None = None,
    ) -> None:
        settings.require_enabled()
        self.settings = settings
        self.inspector = inspector
        self.memory = memory
        self.notifier = notifier
        self.generator = generator or CandidateGenerator()
        self.reviewer = reviewer or SelfReviewer()
        self.clock = clock
        self.cycle_id = cycle_id or uuid4()
        self.since = since
        self.state_root = state_root or settings.state_root
        self.mode = settings.mode
        self.budget = settings.budget
        # Dry run never executes, whatever executor was supplied.
        self.executor: CandidateExecutor = (
            DryRunExecutor() if self.mode is CycleMode.DRY_RUN or executor is None else executor
        )
        self.policy = ShipPolicy(mode=self.mode, budget=self.budget)
        self.usage = CycleUsage()
        self.transitions: list[CycleTransition] = []
        self.phase = CyclePhase.CREATED
        self._started_at = self._now()

    # -- public --------------------------------------------------------------------------

    def run(self) -> tuple[CycleRecord, CycleReport]:
        state = _State()
        head = "0" * 40
        try:
            head = self.inspector.head_sha()
            self._observe(state)
            self._understand(state)
            self._prioritize(state)
            if state.selected is not None:
                self._investigate_and_plan(state)
                self._implement_and_test(state)
                self._self_review(state)
                self._assess_risk(state)
                self._decide(state)
                self._observe_results(state)
            self._learn(state)
        except _BudgetExhausted as exc:
            state.failure = CycleFailure.BUDGET_EXHAUSTED
            state.decision_reasons.append(f"budget exhausted: {exc}")
            self._notify_failure(state, "Engineering cycle stopped at its budget")
        except MemoryStoreError:
            state.failure = CycleFailure.INTERNAL_ERROR
            state.decision_reasons.append("the private memory store failed")
            self._notify_failure(state, "Engineering cycle failed: memory store")
        except ExecutorError as exc:
            state.failure = CycleFailure.EXECUTOR_ERROR
            state.decision_reasons.append(f"executor error: {type(exc).__name__}")
            self._notify_failure(state, "Engineering cycle failed: executor")
        except Exception as exc:  # noqa: BLE001 - the record must always be written
            state.failure = CycleFailure.INTERNAL_ERROR
            state.decision_reasons.append(f"internal error: {type(exc).__name__}")
            self._notify_failure(state, "Engineering cycle failed: internal error")
        record = self._record(state, head)
        report = self._report(record, state)
        # The daily report is the last notification; the record must show it.
        record = record.model_copy(update={"notifications": list(self.notifier.records)[:50]})
        self._persist(record, report)
        return record, report

    # -- phases --------------------------------------------------------------------------

    def _observe(self, state: _State) -> None:
        self._advance(CyclePhase.OBSERVE, "collect repository, check, and gate evidence")
        state.signals = self.inspector.collect(self.since)
        self.usage = self.usage.model_copy(
            update={"tool_calls": self.usage.tool_calls + self.inspector.calls}
        )
        self._check_budget()
        self._notify_regressions(state)

    def _understand(self, state: _State) -> None:
        self._advance(CyclePhase.UNDERSTAND, "read private memory")
        state.memories = self.memory.retrieve(MemoryQuery(limit=50, as_of=self._now()))
        state.memory_reads = len(state.memories)

    def _prioritize(self, state: _State) -> None:
        self._advance(CyclePhase.PRIORITIZE, "rank candidates by value, urgency, confidence, cost")
        state.candidates = self.generator.generate(state.signals, state.memories)
        worthless = 0
        for candidate in state.candidates:
            if candidate.blockers:
                state.abandoned.append(candidate.candidate_id)
                continue
            if candidate.estimate.score <= 0:
                # Never invent busywork: a candidate whose cost outweighs its value waits.
                worthless += 1
                state.abandoned.append(candidate.candidate_id)
                continue
            state.selected = candidate
            break
        if state.selected is None:
            if not state.candidates:
                state.decision_reasons.append("no candidate was found")
            elif worthless == len(state.candidates):
                state.decision_reasons.append("no candidate is worth its cost")
            else:
                state.decision_reasons.append("no unblocked candidate")

    def _investigate_and_plan(self, state: _State) -> None:
        self._advance(CyclePhase.INVESTIGATE, "confirm the candidate against its evidence")
        assert state.selected is not None
        cited = {item.signal_id for item in state.signals}
        if not set(state.selected.signal_ids).issubset(cited):
            raise ExecutorError("The selected candidate cites evidence this cycle did not observe")
        self._advance(CyclePhase.PLAN, "plan the smallest correct change")

    def _implement_and_test(self, state: _State) -> None:
        assert state.selected is not None
        dirty = any(
            item.kind is SignalKind.WORKING_TREE and item.severity is SignalSeverity.WARNING
            for item in state.signals
        )
        self._advance(CyclePhase.IMPLEMENT, "produce the change through the executor")
        if dirty and self.mode is not CycleMode.DRY_RUN:
            raise ExecutorError("The working tree is dirty; refusing to change code on top of it")
        outcome = self.executor.execute(state.selected, cycle_id=self.cycle_id, budget=self.budget)
        state.outcome = outcome
        self.usage = self.usage.model_copy(
            update={
                "tool_calls": self.usage.tool_calls + outcome.tool_calls + 1,
                "model_calls": self.usage.model_calls + outcome.model_calls,
                "input_tokens": self.usage.input_tokens + outcome.input_tokens,
                "output_tokens": self.usage.output_tokens + outcome.output_tokens,
                "cost_usd": (
                    None
                    if outcome.cost_usd is None and self.usage.cost_usd is None
                    else (self.usage.cost_usd or 0.0) + (outcome.cost_usd or 0.0)
                ),
                "changed_files": len(outcome.change.changed_files) if outcome.change else 0,
                "diff_bytes": outcome.change.diff_bytes if outcome.change else 0,
            }
        )
        self._advance(CyclePhase.TEST, "run the repository's validation gates")
        self._check_budget()

    def _self_review(self, state: _State) -> None:
        self._advance(CyclePhase.SELF_REVIEW, "challenge the change with an independent checklist")
        assert state.selected is not None and state.outcome is not None
        outcome = state.outcome
        if outcome.change is not None and outcome.patch is not None:
            state.facts = diff_facts(
                outcome.patch, outcome.change.changed_files, outcome.change.diff_sha256
            )
        else:
            state.facts = DiffFacts(changed_files=list(state.selected.expected_paths))
        tests_ran = any(
            item.gate.value == "pytest_full" and item.status is GateStatus.PASSED
            for item in outcome.gates
        )
        state.review = self.reviewer.review(
            state.facts,
            state.selected.category,
            tests_ran=tests_ran,
            root_cause_evidence=outcome.root_cause_evidence,
        )

    def _assess_risk(self, state: _State) -> None:
        self._advance(CyclePhase.ASSESS_RISK, "classify the change")
        assert state.selected is not None and state.outcome is not None
        change = state.outcome.change
        paths = (
            list(change.changed_files)
            if change is not None
            else list(state.selected.expected_paths)
        )
        state.risk = classify_change(
            category=state.selected.category,
            paths=paths,
            additions=change.additions if change else 0,
            deletions=change.deletions if change else 0,
            diff_bytes=change.diff_bytes if change else 0,
            budget=self.budget,
            derived_from_untrusted_text=state.selected.derived_from_untrusted_text,
        )

    def _decide(self, state: _State) -> None:
        self._advance(CyclePhase.DECIDE, "apply the ship policy")
        assert state.selected is not None and state.outcome is not None and state.risk is not None
        policy = self.policy.decide(
            risk=state.risk,
            gates=state.outcome.gates,
            self_review=state.review,
            usage=self.usage,
            plan_only=state.outcome.change is None,
        )
        if policy.decision is CycleDecision.SHIP and not self.executor.can_ship:
            policy = PolicyDecision(
                CycleDecision.REQUEST_APPROVAL,
                (*policy.reasons, "no publisher is configured, so the owner applies the change"),
            )
        state.policy = policy
        state.decision_reasons.extend(policy.reasons)
        if policy.decision is CycleDecision.REQUEST_APPROVAL:
            state.request = self._approval_request(state)
            self.notifier.notify(
                Notification(
                    event=NotificationEvent.APPROVAL_REQUIRED,
                    title=f"Approval required: {state.selected.title}"[:200],
                    body=render_approval_request(state.request)[:4000],
                    cycle_id=self.cycle_id,
                )
            )
        elif policy.decision is CycleDecision.SHIP:
            assert state.outcome.change is not None
            shipped = self.executor.ship(state.outcome.change, cycle_id=self.cycle_id)
            state.outcome = ExecutionOutcome(
                change=shipped,
                patch=state.outcome.patch,
                gates=state.outcome.gates,
                notes=state.outcome.notes,
                root_cause_evidence=state.outcome.root_cause_evidence,
            )
            self.notifier.notify(
                Notification(
                    event=NotificationEvent.IMPROVEMENT_COMPLETED,
                    title=f"Shipped: {state.selected.title}"[:200],
                    body=(
                        "Low-risk change shipped after every gate passed. "
                        f"{shipped.rollback_reference}"
                    ),
                    cycle_id=self.cycle_id,
                )
            )
        elif policy.decision is CycleDecision.ABANDON:
            state.abandoned.append(state.selected.candidate_id)
        elif policy.decision is CycleDecision.BLOCKED:
            self.notifier.notify(
                Notification(
                    event=NotificationEvent.BLOCKED,
                    title=f"Blocked: {state.selected.title}"[:200],
                    body="; ".join(policy.reasons)[:4000],
                    cycle_id=self.cycle_id,
                )
            )

    def _observe_results(self, state: _State) -> None:
        self._advance(CyclePhase.OBSERVE_RESULTS, "check the shipped change for regressions")
        assert state.policy is not None and state.outcome is not None
        if state.policy.decision is not CycleDecision.SHIP or state.outcome.change is None:
            return
        regressions = [
            item
            for item in state.outcome.gates
            if item.status in {GateStatus.FAILED, GateStatus.ERROR}
        ]
        if regressions:
            state.rollback = self.executor.rollback(
                state.outcome.change, reason="post-ship gate regression"
            )
            self.notifier.notify(
                Notification(
                    event=NotificationEvent.ROLLBACK_OCCURRED,
                    title="Rolled back a shipped change",
                    body=state.rollback.reason,
                    cycle_id=self.cycle_id,
                )
            )

    def _learn(self, state: _State) -> None:
        self._advance(CyclePhase.LEARN, "record validated lessons only")
        now = self._now()
        decision = self._decision(state)
        sequence = 0

        def write(memory: EngineerMemory, summary: str) -> None:
            memory_id, written = self.memory.remember(memory)
            if written:
                state.memory_writes.append(memory_id)
                state.memory_summaries.append(summary)

        recurred = False
        if state.selected is not None:
            sequence += 1
            decision_memory = observation(
                cycle_id=self.cycle_id,
                sequence=sequence,
                category=MemoryCategory.DECISION,
                content=(
                    f"{state.selected.title}: decision {decision.value} because "
                    + "; ".join(state.decision_reasons[:3])
                ),
                now=now,
                confidence=70,
                tags=["decision", decision.value],
            )
            _, written_now = self.memory.remember(decision_memory)
            if written_now:
                state.memory_writes.append(decision_memory.memory_id)
                state.memory_summaries.append(f"decision record for '{state.selected.title}'")
            else:
                # The identical decision was already remembered by an earlier cycle: the
                # same work keeps coming back, which is knowledge in its own right.
                recurred = True
        if recurred and state.selected is not None:
            sequence += 1
            write(
                observation(
                    cycle_id=self.cycle_id,
                    sequence=sequence,
                    category=MemoryCategory.RECURRING_PATTERN,
                    content=(
                        f"{state.selected.title}: recurred with the same outcome "
                        f"({decision.value}) in more than one cycle; likely a recurring "
                        "issue pattern rather than a one-off."
                    ),
                    now=now,
                    confidence=60,
                    tags=["recurring", state.selected.category.value],
                ),
                f"recurring pattern for '{state.selected.title}'",
            )
            state.learned.append(f"{state.selected.title} is a recurring pattern.")
        outcome = state.outcome
        if state.selected is not None and outcome is not None:
            evidence = [item.evidence_sha256 for item in outcome.gates if item.evidence_sha256]
            passed = all(item.status is GateStatus.PASSED for item in outcome.gates) and bool(
                evidence
            )
            if decision is CycleDecision.SHIP and passed:
                sequence += 1
                write(
                    observation(
                        cycle_id=self.cycle_id,
                        sequence=sequence,
                        category=MemoryCategory.ENGINEERING_LESSON,
                        content=(
                            f"{state.selected.title}: shipped after every required gate passed "
                            f"({state.selected.category.value})."
                        ),
                        now=now,
                        confidence=90,
                        tags=["validated", state.selected.category.value],
                        status=EpistemicStatus.VALIDATED_FACT,
                        evidence_sha256=evidence[:20],
                        source=MemorySource.VALIDATION_EVIDENCE,
                    ),
                    "validated lesson from a shipped change",
                )
                state.learned.append(f"{state.selected.title} was safe to ship autonomously.")
            elif decision is CycleDecision.ABANDON:
                sequence += 1
                write(
                    observation(
                        cycle_id=self.cycle_id,
                        sequence=sequence,
                        category=MemoryCategory.ENGINEERING_LESSON,
                        content=(
                            f"{state.selected.title}: abandoned because "
                            + "; ".join(state.decision_reasons[:2])
                        ),
                        now=now,
                        confidence=75,
                        tags=["failed_attempt", state.selected.category.value],
                        status=EpistemicStatus.FAILED_HYPOTHESIS,
                        evidence_sha256=evidence[:20],
                        source=MemorySource.VALIDATION_EVIDENCE
                        if evidence
                        else MemorySource.CYCLE_RECORD,
                    ),
                    "failed hypothesis recorded",
                )
                state.learned.append(f"{state.selected.title} did not survive validation.")
                if state.selected.estimate.confidence >= 70:
                    sequence += 1
                    write(
                        observation(
                            cycle_id=self.cycle_id,
                            sequence=sequence,
                            category=MemoryCategory.SELF_EVALUATION,
                            content=(
                                f"{state.selected.title}: estimated "
                                f"{state.selected.estimate.confidence}% confidence for a "
                                f"{state.selected.category.value} change, but validation "
                                "failed; the estimate was overconfident."
                            ),
                            now=now,
                            confidence=70,
                            tags=["overconfident", state.selected.category.value],
                        ),
                        "self-evaluation: overconfident estimate",
                    )
            if (
                outcome.root_cause_evidence
                and passed
                and decision in {CycleDecision.SHIP, CycleDecision.REQUEST_APPROVAL}
            ):
                sequence += 1
                write(
                    observation(
                        cycle_id=self.cycle_id,
                        sequence=sequence,
                        category=MemoryCategory.ROOT_CAUSE,
                        content=(
                            f"{state.selected.title}: root cause demonstrated; the reproduction "
                            "failed before the change and passed after it, and every gate "
                            f"passed ({state.selected.category.value})."
                        ),
                        now=now,
                        confidence=85,
                        tags=["root_cause", state.selected.category.value],
                        status=EpistemicStatus.VALIDATED_FACT,
                        evidence_sha256=evidence[:20],
                        source=MemorySource.VALIDATION_EVIDENCE,
                    ),
                    "root cause recorded as validated fact",
                )
        for candidate in state.candidates:
            if state.selected is not None and candidate.candidate_id == state.selected.candidate_id:
                continue
            if candidate.derived_from_untrusted_text or candidate.estimate.score <= 0:
                continue
            sequence += 1
            write(
                observation(
                    cycle_id=self.cycle_id,
                    sequence=sequence,
                    category=MemoryCategory.BACKLOG_ITEM,
                    content=f"{candidate.title}: {candidate.rationale[:300]}",
                    now=now,
                    confidence=min(candidate.estimate.confidence, 80),
                    tags=["backlog", candidate.category.value],
                    status=EpistemicStatus.INFERENCE,
                ),
                f"backlog item '{candidate.title}'",
            )

    # -- record and report ---------------------------------------------------------------

    def _decision(self, state: _State) -> CycleDecision:
        if state.failure is not None:
            return CycleDecision.BLOCKED
        if state.selected is None:
            return CycleDecision.NO_WORK
        if state.policy is None:
            return CycleDecision.BLOCKED
        return state.policy.decision

    def _record(self, state: _State, head: str) -> CycleRecord:
        decision = self._decision(state)
        if not state.decision_reasons:
            state.decision_reasons.append("no work was selected")
        self._advance(CyclePhase.REPORT, "write the record and the report", check_budget=False)
        self._advance(CyclePhase.CLOSED, "cycle closed", check_budget=False)
        completed = self._now()
        self.usage = self.usage.model_copy(
            update={
                "runtime_seconds": max(0.0, (completed - self._started_at).total_seconds()),
                "turns": len(self.transitions),
            }
        )
        if self.usage.exceeded(self.budget) and state.failure is None:
            state.failure = CycleFailure.BUDGET_EXHAUSTED
            decision = CycleDecision.BLOCKED
        outcome = state.outcome
        return CycleRecord(
            cycle_id=self.cycle_id,
            mode=self.mode,
            model=self.settings.model,
            repository_head=head,
            started_at=self._started_at,
            completed_at=completed,
            phase_reached=self.phase,
            transitions=list(self.transitions),
            signals=state.signals[:1000],
            candidates=state.candidates[:200],
            ranking=[item.candidate_id for item in state.candidates][:200],
            selected_candidate_id=state.selected.candidate_id if state.selected else None,
            abandoned_candidate_ids=sorted(set(state.abandoned), key=str)[:200],
            risk=state.risk,
            self_review=state.review,
            gates=outcome.gates if outcome else [],
            change=outcome.change if outcome else None,
            decision=decision,
            decision_reasons=[item[:500] for item in state.decision_reasons][:50],
            approval_request=state.request,
            budget=self.budget,
            usage=self.usage,
            memory_reads=state.memory_reads,
            memory_writes=state.memory_writes[:100],
            notifications=list(self.notifier.records)[:50],
            rollback=state.rollback,
            failure=state.failure,
        )

    def _report(self, record: CycleRecord, state: _State) -> CycleReport:
        report = build_report(
            record,
            learned=state.learned,
            memory_added=state.memory_summaries,
            now=record.completed_at,
        )
        self.notifier.flush_daily_report(self.cycle_id, report.text)
        return report

    def _persist(self, record: CycleRecord, report: CycleReport) -> None:
        directory = self.state_root / "cycles"
        directory.mkdir(parents=True, exist_ok=True)
        for name, payload in (
            (f"{record.cycle_id}.json", canonical_json(record)),
            (f"{record.cycle_id}.report.md", report.text),
            ("latest.json", canonical_json(record)),
            ("latest.report.md", report.text),
        ):
            target = directory / name
            temporary = directory / f".{name}.tmp"
            temporary.write_text(payload + "\n", encoding="utf-8")
            os.replace(temporary, target)

    # -- helpers -------------------------------------------------------------------------

    def _approval_request(self, state: _State) -> ApprovalRequest:
        assert state.selected is not None and state.outcome is not None and state.risk is not None
        candidate = state.selected
        change = state.outcome.change
        files = list(change.changed_files) if change is not None else list(candidate.expected_paths)
        reference = (
            f"branch {change.branch}, commit {change.commit_sha}"
            if change is not None and change.branch and change.commit_sha
            else "no branch yet: this is a plan"
        )
        return ApprovalRequest(
            request_id=uuid5(_CYCLE_NAMESPACE, f"{self.cycle_id}:{candidate.candidate_id}:request"),
            cycle_id=self.cycle_id,
            candidate_id=candidate.candidate_id,
            title=candidate.title,
            problem=candidate.rationale,
            root_cause=(
                "Established by the executor."
                if state.outcome.root_cause_evidence
                else "Not yet established; investigation is part of the proposed work."
            ),
            proposed_fix=(
                f"Change {len(files)} file(s): {', '.join(files[:5])}"
                if files
                else "Investigate first; no files are known yet."
            ),
            why=candidate.rationale[:4000],
            files_affected=files[:1000],
            behavior_changed=(
                "No behavior changed yet (plan only)."
                if change is None
                else "See the diff; the self-review lists public-behavior concerns."
            ),
            validation=state.outcome.gates,
            benchmark_impact="not measured" if change is None else "see benchmark gate result",
            security_impact=_security_impact(state.review),
            risk=state.risk,
            confidence=candidate.estimate.confidence,
            rollback_plan=(
                change.rollback_reference if change is not None else "Nothing to roll back."
            ),
            reference=reference,
            question=(
                "Should the engineer proceed with this change?"
                if change is None
                else "Ship this validated change?"
            ),
            recommendation="ship" if state.risk.level is RiskLevel.LOW and change else "revise",
            dry_run=self.mode is CycleMode.DRY_RUN,
            created_at=self._now(),
        )

    def _advance(self, target: CyclePhase, reason: str, *, check_budget: bool = True) -> None:
        if _ORDER.index(target) <= _ORDER.index(self.phase):
            raise RuntimeError(f"Cycle cannot move backwards: {self.phase} -> {target}")
        self.transitions.append(
            CycleTransition(
                sequence=len(self.transitions) + 1,
                source=self.phase,
                target=target,
                reason=reason[:500],
                occurred_at=self._now(),
            )
        )
        self.phase = target
        self.usage = self.usage.model_copy(update={"turns": len(self.transitions)})
        if check_budget:
            self._check_budget()

    def _check_budget(self) -> None:
        elapsed = max(0.0, (self._now() - self._started_at).total_seconds())
        usage = self.usage.model_copy(update={"runtime_seconds": elapsed})
        exceeded = usage.exceeded(self.budget)
        if exceeded:
            raise _BudgetExhausted(exceeded)

    def _notify_regressions(self, state: _State) -> None:
        """Tell the owner about regressions the evidence shows, whatever happens next."""

        failures = [item for item in state.signals if item.severity is SignalSeverity.FAILURE]
        tests = [item for item in failures if item.kind is SignalKind.TEST_RESULTS]
        gates = [
            item
            for item in failures
            if item.kind in {SignalKind.BENCHMARK, SignalKind.E2E_GATE, SignalKind.SENTINEL_GATE}
        ]
        for event, items, title in (
            (NotificationEvent.TEST_REGRESSION, tests, "Test regression observed"),
            (
                NotificationEvent.BENCHMARK_REGRESSION,
                gates,
                "Gate or benchmark regression observed",
            ),
        ):
            if items:
                self.notifier.notify(
                    Notification(
                        event=event,
                        title=title,
                        body="\n".join(f"- {item.summary}" for item in items)[:4000],
                        cycle_id=self.cycle_id,
                    )
                )

    def _notify_failure(self, state: _State, title: str) -> None:
        self.notifier.notify(
            Notification(
                event=NotificationEvent.ENGINEERING_CYCLE_FAILED,
                title=title[:200],
                body="; ".join(state.decision_reasons)[:4000] or "no detail",
                cycle_id=self.cycle_id,
            )
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.utcoffset() is None:
            raise ValueError("Engineering cycle clock must be timezone-aware")
        return value


def _security_impact(review: SelfReview | None) -> str:
    if review is None:
        return "not reviewed"
    notes = [
        item.note
        for item in review.concerns
        if item.question in {ReviewQuestion.SENSITIVE_DATA, ReviewQuestion.PROMPT_INJECTION}
    ]
    return ("; ".join(notes) or "none identified")[:500]


def record_owner_decision(
    store: EngineerMemoryStore,
    *,
    request: ApprovalRequest,
    command: OwnerCommand,
    owner_id: str,
    now: datetime,
) -> OwnerDecision:
    """Turn an authorized owner command into a decision and a remembered preference."""

    authorize_owner_command(command, owner_id=owner_id)
    if command.request_id != request.request_id:
        raise ValueError("Owner command does not answer this approval request")
    decision = OwnerDecision(
        decision_id=command.command_id,
        request_id=request.request_id,
        verdict=command.verdict,
        decided_by=command.issued_by,
        reason=command.reason,
        channel=command.channel,
        decided_at=command.issued_at,
    )
    memory = observation(
        cycle_id=request.cycle_id,
        sequence=1,
        category=MemoryCategory.OWNER_PREFERENCE,
        content=(f"{request.title}: owner decided {decision.verdict.value} ({decision.reason})"),
        now=now,
        confidence=100,
        tags=["owner", decision.verdict.value],
        status=EpistemicStatus.OWNER_DECISION,
        owner_decision_id=decision.decision_id,
        source=MemorySource.OWNER_DECISION,
    ).model_copy(
        update={"memory_id": uuid5(_CYCLE_NAMESPACE, f"{command.command_id}:owner-decision")}
    )
    store.remember(memory)
    return decision


__all__ = [
    "CandidateExecutor",
    "DryRunExecutor",
    "EngineeringCycle",
    "ExecutionOutcome",
    "ExecutorError",
    "record_owner_decision",
]
