"""Reproducible identity for the frozen memoryless investigator baseline."""

import importlib.metadata
import json
import platform
import subprocess
from hashlib import sha256

from nexus import __version__
from nexus.aegisops.config import AgentSettings
from nexus.aegisops.instructions import instruction_hash
from nexus.aegisops.models import Diagnosis
from nexus.aegisops.output_schema import DIAGNOSIS_OUTPUT_SCHEMA
from nexus.aegisops.tools import tool_registry_hash
from nexus.brain.config import BrainSettings
from nexus.brain.manager import AegisOpsBrain, writer_allowlist_sha256
from nexus.brain.models import (
    BRAIN_SCHEMA_VERSION,
    RETRIEVAL_ALGORITHM_VERSION,
    TOKENIZER_IDENTITY,
    WRITER_POLICY_VERSION,
    MemoryRecord,
)
from nexus.brain.retrieval import renderer_template_sha256
from nexus.evaluation.aegisops.benchmark_models import (
    BASELINE_NAME,
    BaselineIdentity,
    BrainIdentity,
)
from nexus.lab.catalog import ScenarioCatalog


def canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(payload.encode("utf-8")).hexdigest()


def repository_state() -> tuple[str, bool]:
    sha = _git(["rev-parse", "HEAD"])
    status = _git(["status", "--porcelain=v1"])
    return sha, bool(status)


def scenario_catalog_hash(catalog: ScenarioCatalog) -> str:
    payload = [scenario.model_dump(mode="json") for scenario in catalog.list()]
    return canonical_hash(payload)


def diagnosis_schema_hash() -> str:
    return canonical_hash(
        {
            "domain": Diagnosis.model_json_schema(),
            "transport": DIAGNOSIS_OUTPUT_SCHEMA.json_schema(),
        }
    )


def build_baseline_identity(
    settings: AgentSettings,
    catalog: ScenarioCatalog,
    *,
    allow_dirty: bool = False,
    brain_settings: BrainSettings | None = None,
) -> BaselineIdentity:
    git_sha, dirty = repository_state()
    if dirty and not allow_dirty:
        raise ValueError(
            "Official benchmark requires a clean Git working tree; "
            "use --allow-dirty only for non-reproducible experimentation"
        )
    resolved_brain = brain_settings or BrainSettings()
    brain_digest: str | None = None
    memory_counts = {}
    if resolved_brain.enabled:
        available, detail, brain_digest = AegisOpsBrain(resolved_brain).check()
        if not available or brain_digest is None:
            raise ValueError(f"Brain identity is unavailable: {detail}")
        memory_counts = AegisOpsBrain(resolved_brain).store.memory_type_counts(
            resolved_brain.namespace
        )
    try:
        preregistration_sha = sha256(resolved_brain.preregistration_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError("Brain v1 pre-registration is unavailable") from exc
    brain_identity = BrainIdentity(
        mode=resolved_brain.mode,
        namespace=(resolved_brain.namespace if resolved_brain.enabled else None),
        schema_version=(BRAIN_SCHEMA_VERSION if resolved_brain.enabled else None),
        memory_record_schema_sha256=(
            canonical_hash(MemoryRecord.model_json_schema()) if resolved_brain.enabled else None
        ),
        writer_policy_version=(WRITER_POLICY_VERSION if resolved_brain.enabled else None),
        writer_allowlist_sha256=(writer_allowlist_sha256() if resolved_brain.enabled else None),
        retrieval_algorithm_version=(
            RETRIEVAL_ALGORITHM_VERSION if resolved_brain.enabled else None
        ),
        retrieval_as_of_policy=("agent_run_started_at" if resolved_brain.enabled else None),
        max_retrieved_memories=(
            resolved_brain.max_retrieved_memories if resolved_brain.enabled else 0
        ),
        max_context_tokens=(resolved_brain.max_context_tokens if resolved_brain.enabled else 0),
        max_context_chars=(resolved_brain.max_context_chars if resolved_brain.enabled else 0),
        tokenizer_identity=(TOKENIZER_IDENTITY if resolved_brain.enabled else None),
        renderer_template_sha256=(renderer_template_sha256() if resolved_brain.enabled else None),
        retrieval_timeout_seconds=(
            resolved_brain.retrieval_timeout_seconds if resolved_brain.enabled else 0
        ),
        snapshot_sha256=brain_digest,
        memory_type_counts=memory_counts,
        preregistration_sha256=preregistration_sha,
    )
    return BaselineIdentity(
        baseline_name=("aegisops-brain-v1" if resolved_brain.enabled else BASELINE_NAME),
        git_sha=git_sha,
        git_dirty=dirty,
        reproducible=not dirty,
        nexus_version=__version__,
        model=settings.model,
        instruction_hash=instruction_hash(),
        tool_registry_hash=tool_registry_hash(),
        diagnosis_schema_hash=diagnosis_schema_hash(),
        scenario_catalog_hash=scenario_catalog_hash(catalog),
        max_turns=settings.max_turns,
        max_tool_calls=settings.max_tool_calls,
        timeout_seconds=settings.timeout_seconds,
        agents_sdk_version=importlib.metadata.version("openai-agents"),
        python_version=platform.python_version(),
        platform=platform.platform(),
        brain_enabled=resolved_brain.enabled,
        brain_namespace=(resolved_brain.namespace if resolved_brain.enabled else None),
        brain_schema_version=(BRAIN_SCHEMA_VERSION if resolved_brain.enabled else None),
        brain_memory_sha256=brain_digest,
        brain_read_only=resolved_brain.read_only,
        brain_max_retrieved_memories=(
            resolved_brain.max_retrieved_memories if resolved_brain.enabled else 0
        ),
        brain_max_context_tokens=(
            resolved_brain.max_context_tokens if resolved_brain.enabled else 0
        ),
        brain_identity=brain_identity,
    )


def _git(arguments: list[str]) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError("Repository Git state is not identifiable")
    return completed.stdout.strip()
