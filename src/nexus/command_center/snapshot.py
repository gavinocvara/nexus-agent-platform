"""Assemble one live ``Snapshot`` from every read-only source."""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from nexus.command_center.config import CommandCenterSettings
from nexus.command_center.models import (
    ActiveRunView,
    ApprovalView,
    BrainMemoryView,
    CycleView,
    EngineerConfigView,
    EngineerMemoryView,
    Fact,
    HealthView,
    InterruptedRunView,
    LabScenarioView,
    MemoryEntryView,
    MemoryView,
    Provenance,
    ServiceHealthView,
    Snapshot,
    SourceState,
    SourceStatus,
    SystemStatus,
    SystemView,
    Tone,
)
from nexus.command_center.sanitize import safe_list, safe_text
from nexus.command_center.sources import (
    EngineerStateReader,
    LabScenario,
    read_brain_counts,
    read_engineer_memory,
    read_lab_scenarios,
)
from nexus.command_center.views import (
    approval_view,
    budget_lines,
    counter_dict,
    cycle_summary,
    cycle_view,
    decision_view,
    effective_decision,
    effective_publication,
    patchforge_ran,
    publication_view,
    sentinel_ran,
)
from nexus.diagnostics.models import HealthState, SystemHealthResult
from nexus.software_engineer.config import SoftwareEngineerSettings
from nexus.software_engineer.models import (
    CycleRecord,
    CycleUsage,
    GateStatus,
    OwnerDecision,
    PublishedChange,
    ValidationGate,
)
from nexus.software_engineer.policy import REQUIRED_GATES_FOR_AUTONOMOUS_SHIP

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def package_version() -> str:
    try:
        return version("nexus-agent-platform")
    except PackageNotFoundError:
        return "unknown"


@dataclass(frozen=True, slots=True)
class HealthSample:
    state: SourceState
    result: SystemHealthResult | None
    observed_at: datetime | None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ReplayStatus:
    state: SourceState
    detail: str | None


class SnapshotBuilder:
    """Reads every source on demand; holds only caches and the latest health sample."""

    def __init__(
        self,
        settings: CommandCenterSettings,
        engineer: SoftwareEngineerSettings,
        *,
        clock: Clock = utc_now,
        environment: Callable[[str], str | None] = os.environ.get,
    ) -> None:
        self.settings = settings
        self.engineer = engineer
        self.clock = clock
        self.environment = environment
        self.state_root = settings.engineer_state_root or engineer.state_root
        self.memory_path = settings.engineer_memory_path or engineer.memory_path
        self.reader = EngineerStateReader(self.state_root)
        self.lab: list[LabScenario] | None = read_lab_scenarios(settings.scenario_directory)
        self._lock = threading.Lock()
        self._health = HealthSample(
            state="preparing" if settings.health_enabled else "disabled",
            result=None,
            observed_at=None,
        )
        self.replay_status = ReplayStatus(
            state="preparing" if settings.replay_enabled else "disabled", detail=None
        )

    # -- inputs from background tasks ---------------------------------------------------

    def set_health(self, sample: HealthSample) -> None:
        with self._lock:
            self._health = sample

    @property
    def health(self) -> HealthSample:
        with self._lock:
            return self._health

    def fingerprint(self) -> tuple[object, ...]:
        health = self.health
        overall = None
        if health.result is not None:
            overall = tuple(
                (item.service.value, item.status.value) for item in health.result.services
            )
        return (
            self.reader.fingerprint(),
            _stat(self.memory_path),
            _stat(self.settings.brain_path),
            health.state,
            overall,
            self.replay_status,
            # A lease expires by time, not by a file change.
            int(self.clock().timestamp() // 30),
        )

    # -- the snapshot -------------------------------------------------------------------

    def build(self, sequence: int) -> Snapshot:
        now = self.clock()
        records = self.reader.records(self.settings.max_cycles)
        decisions = {record.cycle_id: self._decision(record) for record in records}
        publications_by_cycle = {record.cycle_id: self._publication(record) for record in records}
        lease = self.reader.lease()
        active: ActiveRunView | None = None
        if lease is not None and lease.expires_at > now:
            progress = self.reader.progress()
            phase = progress.phase if progress and progress.cycle_id == lease.cycle_id else None
            active = ActiveRunView(
                cycle_id=str(lease.cycle_id),
                started_at=lease.started_at,
                lease_expires_at=lease.expires_at,
                phase=None if phase is None else safe_text(phase, 40),
                phase_source="unobservable" if phase is None else "progress_file",
            )
        interrupted = [
            InterruptedRunView(
                cycle_id=str(item.cycle_id),
                started_at=item.started_at,
                reason=safe_text(item.reason, 200),
                recovered_at=item.recovered_at,
            )
            for item in self.reader.interrupted()
        ]
        summaries = [
            cycle_summary(
                record,
                decision=decisions[record.cycle_id],
                publication=publications_by_cycle[record.cycle_id],
            )
            for record in records
        ]
        latest: CycleView | None = None
        if records:
            record = records[0]
            sentinel = None
            if record.selected_candidate_id is not None:
                sentinel = self.reader.sentinel_verdict(
                    record.cycle_id, record.selected_candidate_id
                )
            latest = cycle_view(
                record,
                decision=decisions[record.cycle_id],
                publication=publications_by_cycle[record.cycle_id],
                sentinel=sentinel,
                report_text=self.reader.report_text(record.cycle_id),
            )
        pending: ApprovalView | None = None
        for record in records:
            if record.approval_request is not None and decisions[record.cycle_id] is None:
                pending = approval_view(record.approval_request, None)
                break
        decision_views = sorted(
            (decision_view(item) for item in decisions.values() if item is not None),
            key=lambda item: item.decided_at,
            reverse=True,
        )[:10]
        publication_views = sorted(
            (publication_view(item) for item in publications_by_cycle.values() if item),
            key=lambda item: item.published_at,
            reverse=True,
        )[:10]
        memory, memory_sources = self._memory()
        health = self._health_view()
        lab = [
            LabScenarioView(
                id=item.id, title=safe_text(item.title, 200), target_service=item.target_service
            )
            for item in (self.lab or [])
        ]
        config = self._config()
        sources = [
            self._engineer_source(records),
            *memory_sources,
            SourceStatus(
                source="lab_health",
                label="Lab service health",
                state=health.state,
                provenance="live",
                detail=self.health.detail,
                observed_at=health.observed_at,
            ),
            SourceStatus(
                source="lab_catalog",
                label="Lab incident catalog",
                state="ok" if self.lab is not None else "unreadable",
                provenance="static",
                detail=f"{len(lab)} scenarios" if self.lab is not None else None,
            ),
            SourceStatus(
                source="replay",
                label="Replay catalog",
                state=self.replay_status.state,
                provenance="replay",
                detail=self.replay_status.detail,
            ),
        ]
        systems = self._systems(
            records=records,
            active=active,
            pending=pending,
            interrupted=interrupted,
            memory=memory,
            health=health,
            lab=lab,
            config=config,
        )
        return Snapshot(
            version=package_version(),
            sequence=sequence,
            generated_at=now,
            sources=sources,
            systems=systems,
            active_run=active,
            interrupted=interrupted,
            latest_cycle=latest,
            cycles=summaries,
            pending_approval=pending,
            decisions=decision_views,
            publications=publication_views,
            memory=memory,
            health=health,
            lab_scenarios=lab,
            engineer_config=config,
        )

    # -- sources ------------------------------------------------------------------------

    def _decision(self, record: CycleRecord) -> OwnerDecision | None:
        if record.approval_request is None:
            return record.owner_decision
        return effective_decision(record, self.reader.decision(record.approval_request.request_id))

    def _publication(self, record: CycleRecord) -> PublishedChange | None:
        stored = None
        if record.approval_request is not None:
            stored = self.reader.publication(record.approval_request.request_id)
        return effective_publication(record, stored)

    def _engineer_source(self, records: list[CycleRecord]) -> SourceStatus:
        if not self.reader.exists():
            return SourceStatus(
                source="engineer_state",
                label="Resident engineer state",
                state="absent",
                provenance="recorded",
                detail="No state tree yet: no cycle has run on this machine.",
            )
        problems = self.reader.problems
        return SourceStatus(
            source="engineer_state",
            label="Resident engineer state",
            state="unreadable" if problems and not records else "ok",
            provenance="recorded",
            detail=(
                f"{len(records)} cycle records"
                + (f"; {len(problems)} unreadable" if problems else "")
            ),
        )

    def _memory(self) -> tuple[MemoryView, list[SourceStatus]]:
        engineer: EngineerMemoryView
        try:
            read = read_engineer_memory(self.memory_path)
        except (sqlite3.Error, ValidationError, ValueError):
            engineer = EngineerMemoryView(state="unreadable")
        else:
            if read is None:
                engineer = EngineerMemoryView(state="absent")
            else:
                engineer = EngineerMemoryView(
                    state="ok",
                    total=sum(item[3] for item in read.counts),
                    by_category=counter_dict([(item[0], item[3]) for item in read.counts]),
                    by_status=counter_dict([(item[1], item[3]) for item in read.counts]),
                    by_lifecycle=counter_dict([(item[2], item[3]) for item in read.counts]),
                    recent=[
                        MemoryEntryView(
                            memory_id=str(item.memory_id),
                            category=item.category.value,
                            status=item.status.value,
                            lifecycle=item.lifecycle.value,
                            confidence=item.confidence,
                            tags=safe_list(list(item.tags), 60, 10),
                            created_at=item.created_at,
                            cycle_id=str(item.provenance.cycle_id),
                            content=safe_text(item.content, 280),
                        )
                        for item in read.recent
                    ],
                )
        brain: BrainMemoryView
        try:
            counts = read_brain_counts(self.settings.brain_path)
        except (sqlite3.Error, ValueError):
            brain = BrainMemoryView(state="unreadable")
        else:
            if counts is None:
                brain = BrainMemoryView(state="absent")
            else:
                brain = BrainMemoryView(
                    state="ok",
                    total=sum(item[2] for item in counts),
                    by_type=counter_dict([(item[0], item[2]) for item in counts]),
                    by_state=counter_dict([(item[1], item[2]) for item in counts]),
                )
        sources = [
            SourceStatus(
                source="engineer_memory",
                label="Engineer private memory",
                state=engineer.state,
                provenance="recorded",
                detail=f"{engineer.total} records" if engineer.state == "ok" else None,
            ),
            SourceStatus(
                source="aegisops_brain",
                label="AegisOps investigator brain",
                state=brain.state,
                provenance="recorded",
                detail=f"{brain.total} records" if brain.state == "ok" else None,
            ),
        ]
        return MemoryView(engineer=engineer, aegisops_brain=brain), sources

    def _health_view(self) -> HealthView:
        sample = self.health
        if sample.result is None:
            return HealthView(
                state=sample.state, overall="unknown", services=[], observed_at=sample.observed_at
            )
        services = [
            ServiceHealthView(
                service=item.service.value,
                status=item.status.value,
                dependencies={key.value: value.value for key, value in item.dependencies.items()},
            )
            for item in sample.result.services
        ]
        statuses = {item.status for item in sample.result.services}
        overall: Literal["healthy", "degraded", "unavailable", "unknown"]
        if statuses == {HealthState.HEALTHY}:
            overall = "healthy"
        elif statuses == {HealthState.UNAVAILABLE}:
            overall = "unavailable"
        elif HealthState.UNHEALTHY in statuses or HealthState.UNAVAILABLE in statuses:
            overall = "degraded"
        else:
            overall = "unknown"
        return HealthView(
            state="ok" if overall != "unavailable" else "unavailable",
            overall=overall,
            services=services,
            observed_at=sample.observed_at,
        )

    def _config(self) -> EngineerConfigView:
        settings = self.engineer
        return EngineerConfigView(
            enabled=settings.enabled,
            mode=settings.mode.value,
            sandbox=settings.sandbox,
            model_configured=settings.model is not None,
            model_recipes_allowed=settings.model_recipes_allowed,
            publish_from_cycle=settings.publish_from_cycle,
            read_issues=settings.read_issues,
            slack_webhook_present=bool(
                (self.environment(settings.slack_webhook_env) or "").strip()
            ),
            slack_owner_configured=settings.slack_owner_user_id is not None,
            github_token_present=bool((self.environment(settings.github_token_env) or "").strip()),
            slack_channel_label=safe_text(settings.slack_channel_label, 100),
            schedule_cron_intent=safe_text(settings.schedule_cron, 100),
            budget=budget_lines(settings.budget, CycleUsage()),
            required_autonomous_gates=sorted(
                item.value for item in REQUIRED_GATES_FOR_AUTONOMOUS_SHIP
            ),
        )

    # -- systems ------------------------------------------------------------------------

    def _systems(
        self,
        *,
        records: list[CycleRecord],
        active: ActiveRunView | None,
        pending: ApprovalView | None,
        interrupted: list[InterruptedRunView],
        memory: MemoryView,
        health: HealthView,
        lab: list[LabScenarioView],
        config: EngineerConfigView,
    ) -> list[SystemView]:
        latest = records[0] if records else None
        forged = next((item for item in records if patchforge_ran(item)), None)
        verified = next((item for item in records if sentinel_ran(item)), None)
        healthy = sum(1 for item in health.services if item.status == "healthy")

        def fact(label: str, value: str, provenance: Provenance, tone: Tone = "neutral") -> Fact:
            return Fact(label=label, value=value, provenance=provenance, tone=tone)

        nexus_status: SystemStatus = "active" if active else ("attention" if pending else "idle")
        nexus = SystemView(
            id="nexus",
            name="NEXUS",
            designation="NX-00",
            role="Platform core: ship policy, owner authority, audit",
            status=nexus_status,
            status_detail=(
                "Dispatching a resident engineer cycle"
                if active
                else "Owner decision pending"
                if pending
                else "Standing by"
            ),
            last_activity_at=latest.completed_at if latest else None,
            facts=[
                fact("Release", package_version(), "static"),
                fact("Cycles recorded", str(len(records)), "recorded"),
                fact(
                    "Owner questions open",
                    "1" if pending else "0",
                    "recorded",
                    "warn" if pending else "neutral",
                ),
                fact("Lab health", health.overall.upper(), "live", _health_tone(health.overall)),
            ],
        )

        aegis_status: SystemStatus
        if health.state == "disabled":
            aegis_status, aegis_detail = "dormant", "Lab health polling is disabled"
        elif health.overall == "unavailable" or health.state == "preparing":
            aegis_status = "offline" if health.overall == "unavailable" else "dormant"
            aegis_detail = (
                "Lab unreachable: the Compose stack is not running"
                if health.overall == "unavailable"
                else "Reading lab health"
            )
        elif health.overall == "degraded":
            aegis_status = "attention"
            aegis_detail = "Lab reports an unhealthy service; no investigation is running"
        else:
            aegis_status, aegis_detail = "dormant", "Lab healthy; no investigation running"
        aegis = SystemView(
            id="aegisops",
            name="AegisOps",
            designation="AO-01",
            role="SRE and incident investigation over typed, read-only diagnostics",
            status=aegis_status,
            status_detail=aegis_detail,
            last_activity_at=health.observed_at,
            facts=[
                fact(
                    "Lab services healthy",
                    f"{healthy}/{len(health.services)}" if health.services else "no reading",
                    "live",
                    _health_tone(health.overall),
                ),
                fact("Incident catalog", f"{len(lab)} deterministic scenarios", "static"),
                fact(
                    "Investigator memory",
                    f"{memory.aegisops_brain.total} records"
                    if memory.aegisops_brain.state == "ok"
                    else memory.aegisops_brain.state,
                    "recorded",
                ),
            ],
        )

        pf_facts = [fact("Change engine", "Attested workspace, runtime-owned Git", "static")]
        if forged is not None:
            pf_gates = [item for item in forged.gates if item.gate.value in _PF]
            passed = sum(1 for item in pf_gates if item.status is GateStatus.PASSED)
            ran = sum(1 for item in pf_gates if item.status is not GateStatus.NOT_RUN)
            pf_facts.append(
                fact(
                    "Last validated gates",
                    f"{passed}/{ran} passed",
                    "recorded",
                    "good" if ran and passed == ran else "warn",
                )
            )
            if forged.change is not None:
                pf_facts.append(
                    fact(
                        "Last change",
                        f"{len(forged.change.changed_files)} files +{forged.change.additions}"
                        f" -{forged.change.deletions}",
                        "recorded",
                    )
                )
        else:
            pf_facts.append(fact("Last change", "none recorded", "recorded"))
        in_forge = active is not None and active.phase in {"implement", "test"}
        patchforge = SystemView(
            id="patchforge",
            name="PatchForge",
            designation="PF-02",
            role="Issue to tested patch inside a disposable, attested workspace",
            status="active" if in_forge else "dormant",
            status_detail=(
                "Producing a change"
                if in_forge
                else "Last produced a change in a recorded cycle"
                if forged
                else "No recorded work yet"
            ),
            last_activity_at=forged.completed_at if forged else None,
            facts=pf_facts,
        )

        sq_status: SystemStatus = "dormant"
        sq_detail = "No recorded verification yet"
        sq_facts = [
            fact("Required for autonomy", "sentinel_review", "static"),
        ]
        if verified is not None:
            review = next(
                (item for item in verified.gates if item.gate is ValidationGate.SENTINEL_REVIEW),
                None,
            )
            status = review.status.value if review else "not_run"
            sq_facts.append(
                fact(
                    "Last review",
                    status.upper(),
                    "recorded",
                    "good"
                    if status == "passed"
                    else "bad"
                    if status in {"failed", "error"}
                    else "neutral",
                )
            )
            if status in {"failed", "error"}:
                sq_status, sq_detail = "attention", "Last review rejected the candidate"
            else:
                sq_detail = f"Last review {status}"
        in_verify = active is not None and active.phase == "test"
        sentinel = SystemView(
            id="sentinelqa",
            name="SentinelQA",
            designation="SQ-03",
            role="Independent verification against the locked pristine specification",
            status="active" if in_verify else sq_status,
            status_detail="Verifying a candidate" if in_verify else sq_detail,
            last_activity_at=verified.completed_at if verified else None,
            facts=sq_facts,
        )

        re_status: SystemStatus
        if active is not None:
            re_status, re_detail = "active", "Cycle in flight"
        elif pending is not None:
            re_status, re_detail = "attention", "Waiting on the owner's decision"
        elif latest is not None:
            re_status, re_detail = "dormant", f"Last cycle: {latest.decision.value}"
        else:
            re_status, re_detail = (
                "dormant",
                ("Enabled; no cycle recorded yet" if config.enabled else "Disabled by default"),
            )
        resident = SystemView(
            id="resident_engineer",
            name="Resident Engineer",
            designation="RE-04",
            role="Bounded daily engineering cycle over this repository",
            status=re_status,
            status_detail=re_detail,
            last_activity_at=latest.completed_at if latest else None,
            facts=[
                fact("Enabled", "YES" if config.enabled else "NO", "static"),
                fact("Mode", config.mode.upper(), "static"),
                fact(
                    "Last decision",
                    latest.decision.value.upper() if latest else "none",
                    "recorded",
                ),
                fact(
                    "Interrupted cycles",
                    str(len(interrupted)),
                    "recorded",
                    "warn" if interrupted else "neutral",
                ),
            ],
        )

        engineer_memory = memory.engineer
        memory_system = SystemView(
            id="memory",
            name="Memory",
            designation="MM-05",
            role="Private per-agent memory: knowledge, never authority",
            status="attention" if engineer_memory.state == "unreadable" else "dormant",
            status_detail=(
                "Engineer memory could not be read"
                if engineer_memory.state == "unreadable"
                else f"{engineer_memory.total + memory.aegisops_brain.total} private records"
            ),
            last_activity_at=(
                engineer_memory.recent[0].created_at if engineer_memory.recent else None
            ),
            facts=[
                fact(
                    "Engineer records",
                    str(engineer_memory.total)
                    if engineer_memory.state == "ok"
                    else engineer_memory.state,
                    "recorded",
                ),
                fact(
                    "Validated facts",
                    str(engineer_memory.by_status.get("validated_fact", 0)),
                    "recorded",
                ),
                fact(
                    "Failed hypotheses",
                    str(engineer_memory.by_status.get("failed_hypothesis", 0)),
                    "recorded",
                ),
                fact(
                    "Investigator records",
                    str(memory.aegisops_brain.total)
                    if memory.aegisops_brain.state == "ok"
                    else memory.aegisops_brain.state,
                    "recorded",
                ),
            ],
        )
        engram = SystemView(
            id="engram",
            name="Engram",
            designation="EG-06",
            role="Curated, provenance-verified cross-agent knowledge",
            status="not_built",
            status_detail="Not built: roadmap Phase 13",
            last_activity_at=None,
            facts=[fact("Status", "Planned, not built", "static")],
        )
        return [nexus, aegis, patchforge, sentinel, resident, memory_system, engram]


_PF = {"ruff_format", "ruff_lint", "mypy", "pytest_targeted", "pytest_full"}


def _health_tone(overall: str) -> Tone:
    if overall == "healthy":
        return "good"
    if overall == "degraded":
        return "bad"
    if overall == "unavailable":
        return "warn"
    return "neutral"


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


__all__ = [
    "HealthSample",
    "ReplayStatus",
    "SnapshotBuilder",
    "package_version",
    "utc_now",
]
