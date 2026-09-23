"""Command-line entry point for evaluator scenario operations."""

import argparse
import json
from collections.abc import Sequence

import httpx

from nexus.lab.catalog import ScenarioCatalog
from nexus.lab.runner import ScenarioRunner, default_service_urls
from nexus.lab.schema import ScenarioService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NEXUS deterministic scenario evaluator")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("list", help="list validated ground-truth scenarios")
    subcommands.add_parser("validate", help="validate every stored scenario")
    for command in ("activate", "run"):
        subparser = subcommands.add_parser(command)
        subparser.add_argument("scenario_id")
    subcommands.add_parser("status", help="inspect both service control states")
    subcommands.add_parser("reset", help="reset all service failure state")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one evaluator CLI command."""

    arguments = _parser().parse_args(argv)
    catalog = ScenarioCatalog.load()
    runner = ScenarioRunner(catalog, default_service_urls())

    if arguments.command == "validate":
        print(f"validated {len(catalog.list())} scenarios")
        return 0
    if arguments.command == "list":
        for scenario in catalog.list():
            print(f"{scenario.id}: {scenario.title}")
        return 0
    if arguments.command == "run":
        print(runner.run(arguments.scenario_id).model_dump_json(indent=2))
        return 0

    with httpx.Client(timeout=10) as client:
        if arguments.command == "activate":
            scenario = catalog.get(arguments.scenario_id)
            runner.activate(scenario, client)
            print(f"activated {scenario.id}")
            return 0
        services = (ScenarioService.USERS, ScenarioService.ORDERS)
        if arguments.command == "status":
            states: dict[str, object] = {}
            for service in services:
                url = f"{default_service_urls()[service]}/__lab/failures/state"
                states[service.value] = client.get(url).json()
            print(json.dumps(states, indent=2))
            return 0
        if arguments.command == "reset":
            for service in services:
                url = f"{default_service_urls()[service]}/__lab/failures/reset-all"
                response = client.post(url)
                response.raise_for_status()
            print("all failures reset")
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
