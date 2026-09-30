"""``python -m nexus.command_center``: serve the dashboard or print a snapshot.

``serve`` binds loopback by default. ``snapshot`` prints the sanitized live snapshot once.
``replay`` captures the curated replay episodes and prints each record hash. All three are
read-only with respect to NEXUS state.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys

from nexus.command_center.config import CommandCenterSettings
from nexus.command_center.replay import ReplayLibrary
from nexus.command_center.snapshot import SnapshotBuilder
from nexus.software_engineer.config import SoftwareEngineerSettings


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nexus.command_center")
    commands = parser.add_subparsers(dest="command", required=True)
    serving = commands.add_parser("serve", help="serve the API, stream, and built client")
    serving.add_argument("--host", default=None)
    serving.add_argument("--port", type=int, default=None)
    commands.add_parser("snapshot", help="print the sanitized live snapshot once")
    commands.add_parser("replay", help="capture replay episodes and print their record hashes")
    arguments = parser.parse_args(argv)
    settings = CommandCenterSettings()
    if arguments.command == "snapshot":
        builder = SnapshotBuilder(settings, SoftwareEngineerSettings())
        print(builder.build(1).model_dump_json(indent=2))
        return 0
    if arguments.command == "replay":
        library = ReplayLibrary()
        library.capture()
        catalog = library.catalog()
        for item in catalog.episodes:
            print(f"{item.name}: decision={item.decision} record_sha256={item.record_sha256}")
        print(f"replay: {catalog.state} ({catalog.detail})")
        return 0 if catalog.state == "ok" else 1
    host = arguments.host or settings.host
    port = arguments.port or settings.port
    if not _is_loopback(host):
        print(
            f"warning: binding {host}; the Command Center has no authentication layer. "
            "Only allowed_hosts may reach it.",
            file=sys.stderr,
        )
    import uvicorn

    from nexus.command_center.app import create_app

    uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
