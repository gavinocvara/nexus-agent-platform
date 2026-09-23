"""Command-line entry point for the single AegisOps investigator."""

import argparse
import asyncio
from collections.abc import Sequence

from nexus.aegisops.models import RunStatus
from nexus.aegisops.runtime import GENERIC_INCIDENT_PROMPT, InvestigatorRuntime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NEXUS AegisOps investigator")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("investigate", help="investigate the current incident")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command != "investigate":
        raise AssertionError("Unhandled AegisOps command")
    record = asyncio.run(InvestigatorRuntime().investigate(GENERIC_INCIDENT_PROMPT))
    print(record.model_dump_json(indent=2))
    return 0 if record.status is RunStatus.COMPLETED else 2


if __name__ == "__main__":
    raise SystemExit(main())
