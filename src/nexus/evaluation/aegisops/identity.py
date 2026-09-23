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
from nexus.evaluation.aegisops.benchmark_models import BaselineIdentity
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
) -> BaselineIdentity:
    git_sha, dirty = repository_state()
    if dirty and not allow_dirty:
        raise ValueError(
            "Official benchmark requires a clean Git working tree; "
            "use --allow-dirty only for non-reproducible experimentation"
        )
    return BaselineIdentity(
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
