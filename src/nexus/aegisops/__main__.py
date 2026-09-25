"""Command-line entry point for the single AegisOps investigator."""

import argparse
import asyncio
from collections.abc import Sequence

from nexus.aegisops.models import RunStatus
from nexus.aegisops.runtime import GENERIC_INCIDENT_PROMPT, InvestigatorRuntime
from nexus.brain.config import BrainSettings
from nexus.brain.models import BrainMode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NEXUS AegisOps investigator")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("investigate", help="investigate the current incident")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command != "investigate":
        raise AssertionError("Unhandled AegisOps command")
    ambient_brain = BrainSettings()
    if ambient_brain.enabled:
        print(
            f"Ad-hoc investigator refuses ambient Brain mode={ambient_brain.mode.value}; "
            "use the guarded benchmark targeted command for writable calibration"
        )
        return 2
    record = asyncio.run(
        InvestigatorRuntime(brain_settings=BrainSettings(mode=BrainMode.DISABLED)).investigate(
            GENERIC_INCIDENT_PROMPT
        )
    )
    print(record.model_dump_json(indent=2))
    return 0 if record.status is RunStatus.COMPLETED else 2


if __name__ == "__main__":
    raise SystemExit(main())
