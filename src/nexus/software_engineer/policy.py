"""Ship policy: the only place that turns evidence into ship / ask / abandon.

The policy is a pure function of runtime-attested inputs. It never reads narrative, never
treats silence as approval, and never lets a change to a governing path ship without a
human decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from nexus.software_engineer.models import (
    ApprovalRequest,
    CycleBudget,
    CycleDecision,
    CycleMode,
    CycleUsage,
    GateResult,
    GateStatus,
    OwnerDecision,
    OwnerVerdict,
    RiskAssessment,
    RiskLevel,
    SelfReview,
    ValidationGate,
)

# The autonomous path has no human before publication, so it needs at least the evidence
# the owner path needs: the repository's own checks and SentinelQA's independent review
# against the pristine specification.
REQUIRED_GATES_FOR_AUTONOMOUS_SHIP = frozenset(
    {
        ValidationGate.RUFF_FORMAT,
        ValidationGate.RUFF_LINT,
        ValidationGate.MYPY,
        ValidationGate.PYTEST_FULL,
        ValidationGate.SENTINEL_REVIEW,
    }
)


class PolicyError(PermissionError):
    """An action outside the ship policy was attempted."""


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision: CycleDecision
    reasons: tuple[str, ...]

    @property
    def ships(self) -> bool:
        return self.decision is CycleDecision.SHIP


def gates_passed(gates: list[GateResult], required: frozenset[ValidationGate]) -> tuple[bool, str]:
    by_gate = {item.gate: item for item in gates}
    failed = sorted(
        item.gate.value for item in gates if item.status in {GateStatus.FAILED, GateStatus.ERROR}
    )
    if failed:
        return False, "failed gates: " + ", ".join(failed)
    missing = sorted(
        gate.value
        for gate in required
        if gate not in by_gate or by_gate[gate].status is not GateStatus.PASSED
    )
    if missing:
        return False, "required gates not passed: " + ", ".join(missing)
    return True, "every required gate passed"


class ShipPolicy:
    """Decide what happens to a validated candidate change."""

    def __init__(self, *, mode: CycleMode, budget: CycleBudget) -> None:
        self.mode = mode
        self.budget = budget

    def decide(
        self,
        *,
        risk: RiskAssessment,
        gates: list[GateResult],
        self_review: SelfReview | None,
        usage: CycleUsage,
        request: ApprovalRequest | None = None,
        owner_decision: OwnerDecision | None = None,
        plan_only: bool = False,
    ) -> PolicyDecision:
        reasons: list[str] = []
        exceeded = usage.exceeded(self.budget)
        if exceeded:
            return PolicyDecision(
                CycleDecision.BLOCKED, (f"budget exceeded: {', '.join(exceeded)}",)
            )
        valid, gate_reason = gates_passed(gates, REQUIRED_GATES_FOR_AUTONOMOUS_SHIP)
        if owner_decision is not None:
            return self._after_owner(owner_decision, request, valid, gate_reason)
        failed = any(item.status in {GateStatus.FAILED, GateStatus.ERROR} for item in gates)
        if self.mode is CycleMode.DRY_RUN and not failed:
            return PolicyDecision(
                CycleDecision.REQUEST_APPROVAL,
                ("dry run: this is a plan, nothing was executed", f"{risk.level.value} risk"),
            )
        if plan_only and not failed:
            return PolicyDecision(
                CycleDecision.REQUEST_APPROVAL,
                (
                    "no change was produced: this is a plan for the owner",
                    f"{risk.level.value} risk",
                ),
            )
        if not valid:
            reasons.append(gate_reason)
            return PolicyDecision(CycleDecision.ABANDON, tuple(reasons))
        reasons.append(gate_reason)
        if risk.governing_paths:
            reasons.append("governing paths need the owner's decision")
            return PolicyDecision(CycleDecision.REQUEST_APPROVAL, tuple(reasons))
        if risk.level is not RiskLevel.LOW:
            reasons.append(f"{risk.level.value} risk needs the owner's decision")
            return PolicyDecision(CycleDecision.REQUEST_APPROVAL, tuple(reasons))
        if self_review is None:
            reasons.append("no self-review was recorded")
            return PolicyDecision(CycleDecision.REQUEST_APPROVAL, tuple(reasons))
        if self_review.requires_human:
            reasons.append("the self-review asked for human judgment")
            return PolicyDecision(CycleDecision.REQUEST_APPROVAL, tuple(reasons))
        if self.mode is not CycleMode.AUTONOMOUS_LOW_RISK:
            reasons.append(f"mode {self.mode.value} never ships autonomously")
            return PolicyDecision(CycleDecision.REQUEST_APPROVAL, tuple(reasons))
        reasons.append("low risk, clean self-review, autonomous mode")
        return PolicyDecision(CycleDecision.SHIP, tuple(reasons))

    def _after_owner(
        self,
        decision: OwnerDecision,
        request: ApprovalRequest | None,
        valid: bool,
        gate_reason: str,
    ) -> PolicyDecision:
        if request is None or decision.request_id != request.request_id:
            raise PolicyError("Owner decision does not answer the current approval request")
        if decision.verdict is OwnerVerdict.REJECT:
            return PolicyDecision(CycleDecision.ABANDON, ("the owner rejected the change",))
        if decision.verdict is OwnerVerdict.REVISE:
            return PolicyDecision(
                CycleDecision.REQUEST_APPROVAL, ("the owner asked for a revision",)
            )
        if not valid:
            return PolicyDecision(
                CycleDecision.ABANDON,
                ("the owner approved, but validation no longer holds: " + gate_reason,),
            )
        return PolicyDecision(CycleDecision.SHIP, ("the owner approved a validated change",))


__all__ = [
    "REQUIRED_GATES_FOR_AUTONOMOUS_SHIP",
    "PolicyDecision",
    "PolicyError",
    "ShipPolicy",
    "gates_passed",
]
