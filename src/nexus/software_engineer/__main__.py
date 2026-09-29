"""``python -m nexus.software_engineer``: preflight, inspect, and run one engineering cycle.

``inspect`` is read-only and works while the engineer is disabled. ``cycle`` refuses to run
unless ``NEXUS_SOFTWARE_ENGINEER_ENABLED=true``; in the default ``dry_run`` mode it changes
no code and, without a Slack webhook in the environment, sends nothing.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from nexus.patchforge.workspace import GitRunner
from nexus.software_engineer.config import EngineerDisabledError, SoftwareEngineerSettings
from nexus.software_engineer.cycle import EngineeringCycle
from nexus.software_engineer.inspect import RepositoryInspector
from nexus.software_engineer.memory import EngineerMemoryStore
from nexus.software_engineer.notify import (
    NotificationTransport,
    Notifier,
    NullTransport,
    SlackWebhookTransport,
)


def _inspector(arguments: argparse.Namespace) -> RepositoryInspector:
    repo = Path(arguments.repo).resolve()
    git = GitRunner(Path(arguments.state_root) / "git-runtime")
    return RepositoryInspector(
        repo,
        git=git,
        clock=lambda: datetime.now(UTC),
        artifacts_dir=Path(arguments.artifacts).resolve() if arguments.artifacts else None,
    )


def _transport(settings: SoftwareEngineerSettings) -> NotificationTransport:
    if os.environ.get(settings.slack_webhook_env, "").strip():
        return SlackWebhookTransport(settings.slack_webhook_env)
    return NullTransport()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NEXUS resident Software Engineer")
    parser.add_argument("--repo", default=".", help="repository checkout to inspect")
    parser.add_argument("--artifacts", default=None, help="directory with validation artifacts")
    parser.add_argument("--since", default=None, help="only consider commits after this SHA")
    parser.add_argument(
        "--state-root", default=None, help="override NEXUS_SOFTWARE_ENGINEER_STATE_ROOT"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight", help="report enablement and configuration; no side effects")
    commands.add_parser("inspect", help="collect and print signals; read-only")
    commands.add_parser("cycle", help="run one bounded engineering cycle")
    arguments = parser.parse_args(argv)
    settings = SoftwareEngineerSettings()
    if arguments.state_root is None:
        arguments.state_root = str(settings.state_root)
    if arguments.command == "preflight":
        slack = bool(os.environ.get(settings.slack_webhook_env, "").strip())
        print(
            f"enabled={settings.enabled} mode={settings.mode.value} owner={settings.owner_id} "
            f"model={settings.model or 'none'} slack_webhook_present={slack} "
            f"state_root={settings.state_root} memory_path={settings.memory_path} "
            f"max_runtime_seconds={settings.max_runtime_seconds} "
            f"max_changed_files={settings.max_changed_files} "
            f"max_diff_bytes={settings.max_diff_bytes}"
        )
        return 0
    if arguments.command == "inspect":
        inspector = _inspector(arguments)
        for signal in inspector.collect(arguments.since):
            flags = " [instruction-like]" if signal.instruction_like else ""
            print(f"{signal.severity.value:7} {signal.kind.value:14} {signal.summary}{flags}")
        return 0
    try:
        settings.require_enabled()
    except EngineerDisabledError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    notifier = Notifier(
        _transport(settings),
        clock=lambda: datetime.now(UTC),
        max_per_cycle=settings.max_notifications_per_cycle,
    )
    cycle = EngineeringCycle(
        settings=settings,
        inspector=_inspector(arguments),
        memory=EngineerMemoryStore(settings.memory_path),
        notifier=notifier,
        clock=lambda: datetime.now(UTC),
        since=arguments.since,
        state_root=Path(arguments.state_root),
    )
    record, report = cycle.run()
    print(report.text)
    print(
        f"cycle={record.cycle_id} decision={record.decision.value} "
        f"failure={record.failure.value if record.failure else 'none'} "
        f"signals={len(record.signals)} candidates={len(record.candidates)} "
        f"memory_writes={len(record.memory_writes)} notifications={len(record.notifications)}"
    )
    return 0 if record.failure is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
