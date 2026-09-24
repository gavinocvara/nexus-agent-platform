"""AegisOps-private Brain orchestration with strict observable inputs."""

import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from nexus.brain.config import BrainSettings
from nexus.brain.models import (
    AGENT_OBSERVABLE_RUN_FIELDS,
    BRAIN_SCHEMA_VERSION,
    RETRIEVAL_ALGORITHM_VERSION,
    TOKENIZER_IDENTITY,
    WRITER_POLICY_VERSION,
    AgentObservableRun,
    BrainMode,
    BrainRunMetadata,
    BrainStorageStatus,
    EpisodicMemory,
    InvestigationCompletion,
    MemoryProvenance,
    MemoryRecord,
    MemoryType,
    ObservableToolCall,
    ProceduralMemory,
    RetrievalStatus,
    RetrievedMemoryAudit,
)
from nexus.brain.retrieval import RetrievalResult, renderer_template_sha256, retrieve_memories
from nexus.brain.storage import (
    MemoryStorageError,
    SQLiteMemoryStore,
    canonical_memory_json,
    memory_content_sha256,
)


@dataclass(frozen=True, slots=True)
class BrainRetrieval:
    prompt: str
    metadata: BrainRunMetadata


@dataclass(frozen=True, slots=True)
class BrainSnapshot:
    path: Path
    file_sha256: str
    canonical_path: Path
    logical_sha256: str
    memory_type_counts: dict[MemoryType, int]


class BrainFailure(RuntimeError):
    """A Brain-enabled run cannot safely continue or complete."""

    def __init__(self, message: str, metadata: BrainRunMetadata) -> None:
        super().__init__(message)
        self.metadata = metadata


def writer_allowlist_sha256() -> str:
    payload = {
        "fields": sorted(AGENT_OBSERVABLE_RUN_FIELDS),
        "schema": AgentObservableRun.model_json_schema(),
        "version": WRITER_POLICY_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


class AegisOpsBrain:
    """Own private retrieval/write behavior without evaluator dependencies."""

    def __init__(
        self,
        settings: BrainSettings | None = None,
        store: SQLiteMemoryStore | None = None,
    ) -> None:
        self.settings = settings or BrainSettings()
        self.store = store or SQLiteMemoryStore(
            self.settings.path,
            read_only=self.settings.read_only,
        )

    async def retrieve(self, prompt: str, *, as_of: datetime) -> BrainRetrieval:
        if self.settings.mode is BrainMode.DISABLED:
            return BrainRetrieval(prompt, BrainRunMetadata())
        started = perf_counter()
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._retrieve, prompt, as_of),
                timeout=self.settings.retrieval_timeout_seconds,
            )
        except TimeoutError as exc:
            raise BrainFailure(
                "Brain retrieval timed out",
                self._failure_metadata(
                    as_of, RetrievalStatus.TIMEOUT, (perf_counter() - started) * 1000
                ),
            ) from exc
        except MemoryStorageError as exc:
            raise BrainFailure(
                "Brain retrieval failed",
                self._failure_metadata(
                    as_of, RetrievalStatus.FAILED, (perf_counter() - started) * 1000
                ),
            ) from exc

    def _retrieve(self, prompt: str, as_of: datetime) -> BrainRetrieval:
        started = perf_counter()
        records = self.store.load_active(self.settings.namespace)
        result = retrieve_memories(
            records,
            prompt,
            max_results=self.settings.max_retrieved_memories,
            max_context_tokens=self.settings.max_context_tokens,
            max_context_chars=self.settings.max_context_chars,
            max_age_days=self.settings.max_age_days,
            as_of=as_of,
        )
        snapshot_sha = self.store.logical_sha256(self.settings.namespace)
        if (
            self.settings.mode is BrainMode.FROZEN_EVAL
            and self.settings.expected_snapshot_sha256 is None
        ):
            raise MemoryStorageError("Frozen Brain requires an expected snapshot identity")
        if (
            self.settings.expected_snapshot_sha256 is not None
            and snapshot_sha != self.settings.expected_snapshot_sha256
        ):
            raise MemoryStorageError("Brain snapshot identity does not match")
        metadata = self._retrieval_metadata(
            result,
            snapshot_sha,
            as_of,
            (perf_counter() - started) * 1000,
        )
        combined = f"{prompt}\n\n{result.context}" if result.context else prompt
        return BrainRetrieval(combined, metadata)

    def write_experience(
        self,
        observation: AgentObservableRun,
        metadata: BrainRunMetadata,
    ) -> BrainRunMetadata:
        """Persist one exact allowlisted projection in learn mode only."""

        if self.settings.mode is BrainMode.DISABLED:
            return metadata
        if self.settings.mode is BrainMode.FROZEN_EVAL:
            raise BrainFailure("Frozen Brain evaluation cannot write", metadata)
        started = perf_counter()
        try:
            episode = _episode_record(self.settings.namespace, observation)
            active_records = self.store.load_active(self.settings.namespace)
            conflicting = _conflicting_episode_ids(active_records, episode)
            if conflicting:
                related_procedures = [
                    record.id
                    for record in active_records
                    if isinstance(record.payload, ProceduralMemory)
                    and set(record.payload.source_episode_ids).intersection(conflicting)
                ]
                records = [episode]
                disputed_ids = [*conflicting, *related_procedures, episode.id]
            else:
                procedure = _procedure_record(episode)
                records = [episode, procedure]
                disputed_ids = []
            written = self.store.apply_experience(records, disputed_ids)
            post_hash = self.store.logical_sha256(self.settings.namespace)
            return metadata.model_copy(
                update={
                    "storage_status": BrainStorageStatus.AVAILABLE,
                    "experience_written": bool(written),
                    "memory_write_count": len(written),
                    "write_latency_ms": (perf_counter() - started) * 1000,
                    "written_memory_ids": written,
                    "post_snapshot_sha256": post_hash,
                    "post_write_sha256": post_hash,
                    "memory_type_counts": self.store.memory_type_counts(self.settings.namespace),
                }
            )
        except MemoryStorageError as exc:
            failed = metadata.model_copy(
                update={
                    "storage_status": BrainStorageStatus.UNAVAILABLE,
                    "experience_written": False,
                    "memory_write_count": 0,
                    "write_latency_ms": (perf_counter() - started) * 1000,
                    "written_memory_ids": [],
                }
            )
            raise BrainFailure("Brain write failed", failed) from exc

    def finish_frozen_run(self, metadata: BrainRunMetadata) -> BrainRunMetadata:
        if self.settings.mode is not BrainMode.FROZEN_EVAL:
            return metadata
        try:
            post_hash = self.store.logical_sha256(self.settings.namespace)
        except MemoryStorageError as exc:
            raise BrainFailure("Frozen Brain verification failed", metadata) from exc
        if post_hash != metadata.pre_snapshot_sha256:
            raise BrainFailure("Frozen Brain snapshot changed during evaluation", metadata)
        return metadata.model_copy(
            update={
                "post_snapshot_sha256": post_hash,
                "post_write_sha256": post_hash,
                "memory_write_count": 0,
                "experience_written": False,
            }
        )

    def check(self) -> tuple[bool, str, str | None]:
        if self.settings.mode is BrainMode.DISABLED:
            return True, BrainMode.DISABLED.value, None
        try:
            self.store.initialize()
            digest = self.store.logical_sha256(self.settings.namespace)
            if (
                self.settings.mode is BrainMode.FROZEN_EVAL
                and self.settings.expected_snapshot_sha256 is None
            ):
                return False, "expected snapshot hash missing", digest
            if (
                self.settings.expected_snapshot_sha256 is not None
                and digest != self.settings.expected_snapshot_sha256
            ):
                return False, "snapshot hash mismatch", digest
            return True, self.settings.mode.value, digest
        except MemoryStorageError:
            return False, "storage unavailable", None

    def snapshot(self, destination: Path) -> BrainSnapshot:
        if self.settings.mode is not BrainMode.LEARN:
            raise MemoryStorageError("Snapshots can only be exported from learn mode")
        file_digest, canonical_path, logical_digest = self.store.snapshot(destination)
        return BrainSnapshot(
            destination,
            file_digest,
            canonical_path,
            logical_digest,
            self.store.memory_type_counts(self.settings.namespace),
        )

    def _retrieval_metadata(
        self,
        result: RetrievalResult,
        snapshot_sha: str,
        as_of: datetime,
        latency_ms: float,
    ) -> BrainRunMetadata:
        records = [item.record for item in result.memories]
        return BrainRunMetadata(
            enabled=True,
            mode=self.settings.mode,
            namespace=self.settings.namespace,
            schema_version=BRAIN_SCHEMA_VERSION,
            writer_policy_version=WRITER_POLICY_VERSION,
            writer_allowlist_sha256=writer_allowlist_sha256(),
            retrieval_algorithm_version=RETRIEVAL_ALGORITHM_VERSION,
            renderer_template_sha256=renderer_template_sha256(),
            tokenizer_identity=TOKENIZER_IDENTITY,
            storage_status=(
                BrainStorageStatus.READ_ONLY
                if self.settings.mode is BrainMode.FROZEN_EVAL
                else BrainStorageStatus.AVAILABLE
            ),
            retrieval_status=(RetrievalStatus.OK if records else RetrievalStatus.EMPTY),
            retrieval_query_count=1,
            retrieval_latency_ms=latency_ms,
            retrieval_as_of=as_of,
            max_retrieved_memories=self.settings.max_retrieved_memories,
            max_context_tokens=self.settings.max_context_tokens,
            max_context_chars=self.settings.max_context_chars,
            retrieved_memories=[
                RetrievedMemoryAudit(
                    id=record.id,
                    memory_type=record.memory_type,
                    content_sha256=memory_content_sha256(record),
                )
                for record in records
            ],
            retrieved_memory_ids=[record.id for record in records],
            retrieved_memory_types=[record.memory_type for record in records],
            procedural_memory_ids=[
                record.id for record in records if record.memory_type is MemoryType.PROCEDURAL
            ],
            retrieval_count=len(records),
            rendered_context_sha256=result.context_sha256,
            rendered_context_chars=result.rendered_chars,
            serialized_context_bytes=result.serialized_bytes,
            estimated_context_tokens=result.estimated_tokens,
            brain_attributable_input_tokens=result.estimated_tokens,
            context_truncated=result.truncated,
            pre_snapshot_sha256=snapshot_sha,
            snapshot_sha256=snapshot_sha,
            memory_type_counts=self.store.memory_type_counts(self.settings.namespace),
        )

    def _failure_metadata(
        self, as_of: datetime, status: RetrievalStatus, latency_ms: float
    ) -> BrainRunMetadata:
        return BrainRunMetadata(
            enabled=True,
            mode=self.settings.mode,
            namespace=self.settings.namespace,
            schema_version=BRAIN_SCHEMA_VERSION,
            writer_policy_version=WRITER_POLICY_VERSION,
            writer_allowlist_sha256=writer_allowlist_sha256(),
            retrieval_algorithm_version=RETRIEVAL_ALGORITHM_VERSION,
            renderer_template_sha256=renderer_template_sha256(),
            tokenizer_identity=TOKENIZER_IDENTITY,
            storage_status=BrainStorageStatus.UNAVAILABLE,
            retrieval_status=status,
            retrieval_query_count=1,
            retrieval_latency_ms=latency_ms,
            retrieval_as_of=as_of,
            max_retrieved_memories=self.settings.max_retrieved_memories,
            max_context_tokens=self.settings.max_context_tokens,
            max_context_chars=self.settings.max_context_chars,
        )


def _episode_record(
    namespace: Literal["aegisops.investigator"], observation: AgentObservableRun
) -> MemoryRecord:
    path = [event.tool for event in observation.tool_calls]
    payload = EpisodicMemory(
        diagnosis=observation.diagnosis,
        evidence_patterns=[_evidence_pattern(event) for event in observation.tool_calls],
        tools_used=list(dict.fromkeys(path)),
        tool_call_count=len(observation.tool_calls),
        diagnostic_path=path,
        completion=observation.runtime_outcome,
    )
    provenance = MemoryProvenance(
        agent_run_id=observation.agent_run_id,
        tool_call_ids=[event.tool_call_id for event in observation.tool_calls],
    )
    record = MemoryRecord(
        id=uuid5(NAMESPACE_URL, f"{namespace}:episode:{observation.agent_run_id}"),
        namespace=namespace,
        memory_type=MemoryType.EPISODIC,
        created_at=observation.finished_at,
        valid_from=observation.started_at,
        valid_to=observation.finished_at,
        payload=payload,
        provenance=provenance,
    )
    MemoryRecord.model_validate_json(canonical_memory_json(record))
    return record


def _procedure_record(episode: MemoryRecord) -> MemoryRecord:
    episodic = EpisodicMemory.model_validate(episode.payload)
    counts = Counter(episodic.diagnostic_path)
    redundant = [f"{tool}:repeated:{count}" for tool, count in sorted(counts.items()) if count > 1]
    risk = (
        ["->".join(episodic.diagnostic_path)]
        if episodic.completion is InvestigationCompletion.TOOL_BUDGET_EXHAUSTED
        and episodic.diagnostic_path
        else []
    )
    diagnosis = episodic.diagnosis
    triggers = ["investigate", "diagnostic"]
    if diagnosis is not None:
        triggers.extend(
            value for value in (diagnosis.component, diagnosis.failure_class) if value is not None
        )
    payload = ProceduralMemory(
        trigger_terms=list(dict.fromkeys(triggers)),
        candidate_next_steps=list(dict.fromkeys(episodic.diagnostic_path)),
        redundant_patterns=redundant,
        budget_risk_sequences=risk,
        evidence_combinations=episodic.evidence_patterns[:5],
        source_episode_ids=[episode.id],
    )
    return MemoryRecord(
        id=uuid5(NAMESPACE_URL, f"{episode.namespace}:procedure:{episode.id}"),
        namespace=episode.namespace,
        memory_type=MemoryType.PROCEDURAL,
        created_at=episode.created_at,
        valid_from=episode.valid_from,
        valid_to=episode.valid_to,
        payload=payload,
        provenance=episode.provenance,
    )


def _conflicting_episode_ids(records: list[MemoryRecord], candidate: MemoryRecord) -> list[UUID]:
    candidate_payload = EpisodicMemory.model_validate(candidate.payload)
    candidate_claim = candidate_payload.diagnosis
    if candidate_claim is None or candidate_claim.status != "diagnosed":
        return []
    conflicting: list[UUID] = []
    for record in records:
        if record.memory_type is not MemoryType.EPISODIC:
            continue
        payload = EpisodicMemory.model_validate(record.payload)
        claim = payload.diagnosis
        if (
            claim is not None
            and claim.status == "diagnosed"
            and payload.diagnostic_path == candidate_payload.diagnostic_path
            and (claim.component, claim.failure_class)
            != (candidate_claim.component, candidate_claim.failure_class)
        ):
            conflicting.append(record.id)
    return conflicting


def _evidence_pattern(event: ObservableToolCall) -> str:
    result = "success" if event.success else "failure"
    error = event.backend_error_code or "none"
    status = event.backend_status_code if event.backend_status_code is not None else "none"
    return (
        f"{event.tool}:{event.backend}:{result}:results-{event.result_count}:"
        f"error-{error}:status-{status}"
    )
