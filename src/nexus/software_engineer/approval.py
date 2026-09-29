"""Owner decisions and publication of approved changes, from persisted cycle state.

Two human-run steps stand between an approval request and a draft pull request. ``decide``
records the owner's SHIP, REVISE, or REJECT as a typed decision and an owner-preference
memory. ``publish`` opens the draft pull request only for a SHIP, only for a change whose
recorded gates all passed, only once, and only after runtime code re-derives the tree from
the validated patch. Silence never publishes anything; neither does REVISE or REJECT.
"""

from __future__ import annotations

import os
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid5

from nexus.atlas.models import ActorIdentity, ActorType
from nexus.patchforge.canonical import canonical_json, canonical_sha256
from nexus.patchforge.workspace import GitRunner
from nexus.software_engineer.cycle import record_owner_decision
from nexus.software_engineer.memory import (
    EngineerMemoryStore,
    EpistemicStatus,
    MemoryCategory,
    MemorySource,
    observation,
)
from nexus.software_engineer.models import (
    ChangeSummary,
    CycleRecord,
    GateStatus,
    OwnerDecision,
    OwnerVerdict,
    PublishedChange,
    ValidationGate,
)
from nexus.software_engineer.policy import REQUIRED_GATES_FOR_AUTONOMOUS_SHIP
from nexus.software_engineer.publish import (
    Publisher,
    PublishError,
    PublishSubmission,
    bundle_from_branch,
    render_pull_request,
)
from nexus.software_engineer.trust import OwnerCommand

_APPROVAL_NAMESPACE = UUID("b1c2d3e4-f5a6-4b7c-8d9e-0f1a2b3c4d5e")
REQUIRED_GATES_FOR_PUBLICATION = frozenset(
    {*REQUIRED_GATES_FOR_AUTONOMOUS_SHIP, ValidationGate.SENTINEL_REVIEW}
)


class ApprovalError(RuntimeError):
    """A decision or publication was refused; ``code`` is stable."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        super().__init__(code if detail is None else f"{code}: {detail}")


# -- persisted state ---------------------------------------------------------------------


def cycle_record_path(state_root: Path, cycle: str) -> Path:
    if cycle == "latest":
        return state_root / "cycles" / "latest.json"
    try:
        cycle_id = UUID(cycle)
    except ValueError as exc:
        raise ApprovalError("cycle_invalid") from exc
    return state_root / "cycles" / f"{cycle_id}.json"


def load_cycle_record(state_root: Path, cycle: str = "latest") -> CycleRecord:
    path = cycle_record_path(state_root, cycle)
    try:
        return CycleRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ApprovalError("cycle_not_found", cycle) from exc
    except (OSError, ValueError) as exc:
        raise ApprovalError("cycle_unreadable", type(exc).__name__) from exc


def load_owner_decision(state_root: Path, request_id: UUID) -> OwnerDecision | None:
    path = state_root / "decisions" / f"{request_id}.json"
    if not path.exists():
        return None
    try:
        return OwnerDecision.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ApprovalError("decision_unreadable", type(exc).__name__) from exc


def load_publication(state_root: Path, request_id: UUID) -> PublishedChange | None:
    path = state_root / "publications" / f"{request_id}.json"
    if not path.exists():
        return None
    try:
        return PublishedChange.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ApprovalError("publication_unreadable", type(exc).__name__) from exc


def _write_json(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp"
    temporary.write_text(payload + "\n", encoding="utf-8")
    os.replace(temporary, path)


# -- owner decision ----------------------------------------------------------------------


def decide(
    *,
    state_root: Path,
    memory: EngineerMemoryStore,
    owner_id: str,
    cycle: str,
    verdict: OwnerVerdict,
    reason: str,
    now: datetime,
) -> OwnerDecision:
    """Record the human owner's answer to the cycle's approval request. Once."""

    record = load_cycle_record(state_root, cycle)
    request = record.approval_request
    if request is None:
        raise ApprovalError("no_approval_request", str(record.cycle_id))
    if load_owner_decision(state_root, request.request_id) is not None:
        raise ApprovalError("already_decided", str(request.request_id))
    text = " ".join(reason.split())
    if not text:
        raise ApprovalError("reason_required")
    command = OwnerCommand(
        command_id=uuid5(
            _APPROVAL_NAMESPACE, f"{request.request_id}:{verdict.value}:{now.isoformat()}"
        ),
        request_id=request.request_id,
        verdict=verdict,
        issued_by=ActorIdentity(actor_type=ActorType.HUMAN, actor_id=owner_id),
        channel="cli",
        reason=text[:500],
        issued_at=now,
    )
    decision = record_owner_decision(
        memory, request=request, command=command, owner_id=owner_id, now=now
    )
    _write_json(state_root / "decisions" / f"{request.request_id}.json", canonical_json(decision))
    return decision


# -- publication -------------------------------------------------------------------------


def publish_approved_change(
    *,
    state_root: Path,
    memory: EngineerMemoryStore,
    owner_id: str,
    cycle: str,
    publisher: Publisher,
    git: GitRunner,
    now: datetime,
    allow_moved_base: bool = False,
    run_root: Path | None = None,
) -> PublishedChange:
    """Open a draft pull request for a change the owner said SHIP to. Fail closed."""

    record = load_cycle_record(state_root, cycle)
    request = record.approval_request
    change = record.change
    if request is None or change is None or record.selected_candidate_id is None:
        raise ApprovalError("nothing_to_publish", str(record.cycle_id))
    if change.branch is None or change.commit_sha is None:
        raise ApprovalError("nothing_to_publish", "the change has no local branch")
    if load_publication(state_root, request.request_id) is not None:
        raise ApprovalError("already_published", str(request.request_id))
    decision = load_owner_decision(state_root, request.request_id)
    if decision is None:
        raise ApprovalError("not_decided", str(request.request_id))
    if decision.verdict is not OwnerVerdict.SHIP:
        raise ApprovalError("not_approved", decision.verdict.value)
    owner = ActorIdentity(actor_type=ActorType.HUMAN, actor_id=owner_id)
    if decision.decided_by != owner:
        raise ApprovalError("decision_owner_mismatch")
    _require_gates(record)
    candidate = next(
        item for item in record.candidates if item.candidate_id == record.selected_candidate_id
    )
    root = (
        (run_root or state_root / "runs") / str(record.cycle_id) / candidate.candidate_id.hex[:12]
    )
    patch = _read_patch(root, change)
    try:
        bundle = bundle_from_branch(
            git,
            root / "branch",
            base_sha=change.base_sha,
            commit_sha=change.commit_sha,
            patch=patch,
            work_root=root / "publish",
        )
        title, body = render_pull_request(
            candidate=candidate,
            change=change,
            gates=record.gates,
            request=request,
            decision=decision,
            cycle_id=record.cycle_id,
        )
        published = publisher.publish(
            PublishSubmission(
                bundle=bundle,
                branch=change.branch,
                title=title,
                body=body,
                cycle_id=record.cycle_id,
                diff_sha256=change.diff_sha256,
                published_by=owner,
                authority="owner_decision",
                request_id=request.request_id,
                decision_id=decision.decision_id,
                allow_moved_base=allow_moved_base,
            )
        )
    except PublishError as exc:
        raise ApprovalError(f"publish_{exc.code}") from exc
    _write_json(
        state_root / "publications" / f"{request.request_id}.json", canonical_json(published)
    )
    memory.remember(
        observation(
            cycle_id=record.cycle_id,
            sequence=1,
            category=MemoryCategory.DECISION,
            content=(
                f"{candidate.title}: published as draft pull request "
                f"#{published.pull_request_number} ({published.pull_request_url}) after the "
                f"owner decided ship; base {published.base_sha[:12]}, "
                f"remote commit {published.remote_commit_sha[:12]}."
            ),
            now=now,
            confidence=100,
            tags=["published", "draft_pull_request", candidate.category.value],
            status=EpistemicStatus.VALIDATED_FACT,
            evidence_sha256=[canonical_sha256(published)],
            owner_decision_id=decision.decision_id,
            source=MemorySource.OWNER_DECISION,
        ).model_copy(
            update={
                "memory_id": uuid5(_APPROVAL_NAMESPACE, f"{published.publication_id}:publication")
            }
        )
    )
    return published


def _require_gates(record: CycleRecord) -> None:
    statuses = {item.gate: item.status for item in record.gates}
    missing = sorted(gate.value for gate in REQUIRED_GATES_FOR_PUBLICATION if gate not in statuses)
    if missing:
        raise ApprovalError("gates_missing", ", ".join(missing))
    failed = sorted(
        gate.value for gate, status in statuses.items() if status is not GateStatus.PASSED
    )
    if failed:
        raise ApprovalError("gates_not_passed", ", ".join(failed))


def _read_patch(root: Path, change: ChangeSummary) -> bytes:
    try:
        patch = (root / "candidate.patch").read_bytes()
    except FileNotFoundError as exc:
        raise ApprovalError("patch_missing", str(root)) from exc
    except OSError as exc:
        raise ApprovalError("patch_unreadable", type(exc).__name__) from exc
    if sha256(patch).hexdigest() != change.diff_sha256 or len(patch) != change.diff_bytes:
        raise ApprovalError("patch_mismatch")
    return patch


__all__ = [
    "REQUIRED_GATES_FOR_PUBLICATION",
    "ApprovalError",
    "cycle_record_path",
    "decide",
    "load_cycle_record",
    "load_owner_decision",
    "load_publication",
    "publish_approved_change",
]
