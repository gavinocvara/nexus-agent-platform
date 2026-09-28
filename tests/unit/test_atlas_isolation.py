"""Atlas additions must not mutate frozen AegisOps or Brain v1 identities."""

from hashlib import sha256
from pathlib import Path

from nexus.aegisops.instructions import instruction_hash
from nexus.aegisops.tools import tool_registry_hash
from nexus.brain.manager import writer_allowlist_sha256
from nexus.brain.models import RETRIEVAL_ALGORITHM_VERSION, MemoryRecord
from nexus.brain.retrieval import renderer_template_sha256
from nexus.evaluation.aegisops.identity import (
    canonical_hash,
    diagnosis_schema_hash,
    scenario_catalog_hash,
)
from nexus.lab.catalog import ScenarioCatalog


def test_phase_5_and_phase_7_frozen_behavior_identities_are_unchanged() -> None:
    assert instruction_hash() == "d858a63116e8579a4e19a19f01cea5441aa840213380455449528ca385dad456"
    assert (
        tool_registry_hash() == "b75d71fff306ab51f445eba5d9a438dc394d02c810ec7fceb4110975a15cbfbf"
    )
    assert (
        diagnosis_schema_hash()
        == "aea17a6803fe3c49f1d1e3268b5f58072e05b5edd5dce9fb72514b96a5b720ab"
    )
    assert scenario_catalog_hash(ScenarioCatalog.load()) == (
        "6b47e28ee3d5c13619903d9885212022c49940224e333188d177a40e46214e2c"
    )
    assert canonical_hash(MemoryRecord.model_json_schema()) == (
        "41c00e96ba305e6116151b38c0abec09d4ca53a37cc84493d31544b73dfac85a"
    )
    assert writer_allowlist_sha256() == (
        "456231e1dc2f68002573f416145e5d92b1418f2763e4fcb0c78e5e0127bcf3cd"
    )
    assert RETRIEVAL_ALGORITHM_VERSION == "bounded-lexical-v1"
    assert renderer_template_sha256() == (
        "600763b679f92ee9304358c666ff3e674f7859847e9f493933255a1df58227ee"
    )
    protocol = Path("docs/experiments/brain-v1-protocol.json")
    assert sha256(protocol.read_bytes()).hexdigest() == (
        "c6c3cff80da44c6fbc486a57b4eaf1ea6215c1ba923c1afed75d6717f94368ed"
    )
