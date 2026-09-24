"""Operator CLI for private Brain inspection and frozen snapshots."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from nexus.brain.config import BrainSettings
from nexus.brain.manager import AegisOpsBrain
from nexus.brain.models import BrainMode
from nexus.brain.storage import MemoryStorageError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NEXUS private Brain tooling")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect", help="show safe private-memory metadata")
    snapshot = commands.add_parser("snapshot", help="create a frozen SQLite snapshot")
    snapshot.add_argument("destination", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    settings = BrainSettings()
    brain = AegisOpsBrain(settings)
    try:
        if arguments.command == "snapshot":
            if settings.mode is not BrainMode.LEARN:
                print("Snapshot export requires NEXUS_BRAIN_MODE=learn")
                return 2
            snapshot = brain.snapshot(arguments.destination)
            print(
                json.dumps(
                    {
                        "path": str(snapshot.path),
                        "file_sha256": snapshot.file_sha256,
                        "canonical_path": str(snapshot.canonical_path),
                        "logical_sha256": snapshot.logical_sha256,
                        "memory_type_counts": snapshot.memory_type_counts,
                    },
                    sort_keys=True,
                )
            )
            return 0
        available, detail, digest = brain.check()
        records = (
            brain.store.load_active(settings.namespace)
            if available and settings.mode is not BrainMode.DISABLED
            else []
        )
        print(
            json.dumps(
                {
                    "available": available,
                    "mode": settings.mode.value,
                    "detail": detail,
                    "namespace": settings.namespace,
                    "active_memory_count": len(records),
                    "logical_sha256": digest,
                },
                sort_keys=True,
            )
        )
        return 0 if available else 2
    except MemoryStorageError:
        print("Brain storage operation failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
