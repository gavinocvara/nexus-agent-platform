"""``python -m nexus.software_engineer``: preflight, inspect, cycle, decide, and publish.

``inspect`` is read-only and works while the engineer is disabled. ``cycle`` refuses to run
unless ``NEXUS_SOFTWARE_ENGINEER_ENABLED=true``; in the default ``dry_run`` mode it changes
no code and, without a Slack webhook in the environment, sends nothing. ``decide`` records
the human owner's SHIP, REVISE, or REJECT for a cycle's approval request. ``publish`` opens
a draft pull request for a SHIP, with a GitHub token that exists only in the environment.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from nexus.patchforge.engine import EngineBudget, ModelBackedEngine
from nexus.patchforge.live import API_KEY_VARIABLE, LiveSettings, OpenAIResponsesClient
from nexus.patchforge.runtime import RuntimeEngine
from nexus.patchforge.workspace import GitRunner
from nexus.software_engineer.approval import ApprovalError, decide, publish_approved_change
from nexus.software_engineer.config import EngineerDisabledError, SoftwareEngineerSettings
from nexus.software_engineer.cycle import CandidateExecutor, EngineeringCycle
from nexus.software_engineer.executor import PatchForgeExecutor
from nexus.software_engineer.inspect import RepositoryInspector
from nexus.software_engineer.issues import GitHubIssueSource
from nexus.software_engineer.memory import EngineerMemoryStore
from nexus.software_engineer.models import CycleMode, OwnerVerdict
from nexus.software_engineer.notify import (
    NotificationTransport,
    Notifier,
    NullTransport,
    SlackWebhookTransport,
)
from nexus.software_engineer.publish import (
    GitHubDraftPullRequestPublisher,
    Publisher,
    PublishError,
    parse_github_repository,
)
from nexus.software_engineer.sandbox import LocalProcessSandbox
from nexus.software_engineer.slack_commands import SlackCommandHandler, create_app


def _issue_source(settings: SoftwareEngineerSettings) -> GitHubIssueSource | None:
    """Issue intake is the engineer's only network read; it exists only when opted in."""

    if not settings.read_issues:
        return None
    return GitHubIssueSource(
        settings.repository_url,
        token_env=settings.github_read_token_env,
        max_issues=settings.max_issues,
    )


def _inspector(
    arguments: argparse.Namespace, settings: SoftwareEngineerSettings
) -> RepositoryInspector:
    repo = Path(arguments.repo).resolve()
    git = GitRunner(Path(arguments.state_root) / "git-runtime")
    return RepositoryInspector(
        repo,
        git=git,
        clock=lambda: datetime.now(UTC),
        artifacts_dir=Path(arguments.artifacts).resolve() if arguments.artifacts else None,
        issue_source=_issue_source(settings),
    )


def _model_engine_factory(
    settings: SoftwareEngineerSettings,
) -> Callable[[], RuntimeEngine] | None:
    """Model recipes need a model, a confirmed spend, positive budgets, and the key."""

    if not settings.model_recipes_allowed or not os.environ.get(API_KEY_VARIABLE, "").strip():
        return None
    assert settings.model is not None
    live = LiveSettings(model=settings.model, timeout_seconds=settings.model_timeout_seconds)
    budget = EngineBudget(
        max_model_calls=settings.max_model_calls,
        max_output_tokens=min(settings.model_max_output_tokens, settings.max_output_tokens),
        max_rendered_result_chars=8000,
        max_history_turns=12,
    )

    def build() -> RuntimeEngine:
        return ModelBackedEngine(
            client=OpenAIResponsesClient.from_environment(live), model=live.model, budget=budget
        )

    return build


def _slack_handler(settings: SoftwareEngineerSettings, state_root: Path) -> SlackCommandHandler:
    return SlackCommandHandler(
        state_root=state_root,
        memory=EngineerMemoryStore(settings.memory_path),
        owner_id=settings.owner_id,
        slack_owner_user_id=settings.slack_owner_user_id,
        signing_secret_env=settings.slack_signing_secret_env,
        window_seconds=settings.slack_replay_window_seconds,
    )


def _github_token_present(settings: SoftwareEngineerSettings) -> bool:
    return bool(os.environ.get(settings.github_token_env, "").strip())


def _publisher(settings: SoftwareEngineerSettings) -> Publisher:
    """A GitHub draft-pull-request publisher; the token is read only when it publishes."""

    return GitHubDraftPullRequestPublisher(
        parse_github_repository(settings.repository_url), token_env=settings.github_token_env
    )


def _cycle_publisher(settings: SoftwareEngineerSettings) -> Publisher | None:
    """Cycles publish only when the owner opted in, in autonomous mode, with a token."""

    if (
        not settings.publish_from_cycle
        or settings.mode is not CycleMode.AUTONOMOUS_LOW_RISK
        or not _github_token_present(settings)
    ):
        return None
    return _publisher(settings)


def _executor(
    settings: SoftwareEngineerSettings, arguments: argparse.Namespace
) -> CandidateExecutor | None:
    """A real executor only outside dry run and only with an explicitly enabled sandbox."""

    if settings.mode is CycleMode.DRY_RUN or settings.sandbox != "local_process":
        return None
    state_root = Path(arguments.state_root)
    return PatchForgeExecutor(
        repo_root=Path(arguments.repo).resolve(),
        repository_url=settings.repository_url,
        sandbox=LocalProcessSandbox(allow_local_process=True),
        run_root=state_root / "runs",
        git=GitRunner(state_root / "git-runtime"),
        model_engine_factory=_model_engine_factory(settings),
        publisher=_cycle_publisher(settings),
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
    deciding = commands.add_parser(
        "decide", help="record the owner's SHIP, REVISE, or REJECT for a cycle's request"
    )
    deciding.add_argument("--cycle", default="latest", help="cycle id, or 'latest'")
    deciding.add_argument("--verdict", required=True, choices=[item.value for item in OwnerVerdict])
    deciding.add_argument("--reason", required=True, help="why; remembered as a preference")
    publishing = commands.add_parser(
        "publish", help="open a draft pull request for a cycle the owner decided to ship"
    )
    publishing.add_argument("--cycle", default="latest", help="cycle id, or 'latest'")
    publishing.add_argument(
        "--allow-moved-base",
        action="store_true",
        help="publish even though the default branch moved past the validated base",
    )
    serving = commands.add_parser(
        "serve-slack", help="serve signature-verified Slack owner commands (ship/revise/reject)"
    )
    serving.add_argument("--host", default="127.0.0.1")
    serving.add_argument("--port", type=int, default=8787)
    arguments = parser.parse_args(argv)
    settings = SoftwareEngineerSettings()
    if arguments.state_root is None:
        arguments.state_root = str(settings.state_root)
    if arguments.command == "preflight":
        slack = bool(os.environ.get(settings.slack_webhook_env, "").strip())
        print(
            f"enabled={settings.enabled} mode={settings.mode.value} owner={settings.owner_id} "
            f"model={settings.model or 'none'} model_recipes_allowed="
            f"{settings.model_recipes_allowed} sandbox={settings.sandbox} "
            f"slack_webhook_present={slack} "
            f"github_token_present={_github_token_present(settings)} "
            f"publish_from_cycle={settings.publish_from_cycle} "
            f"read_issues={settings.read_issues} "
            f"slack_owner_user_id_set={settings.slack_owner_user_id is not None} "
            f"state_root={settings.state_root} memory_path={settings.memory_path} "
            f"max_runtime_seconds={settings.max_runtime_seconds} "
            f"max_changed_files={settings.max_changed_files} "
            f"max_diff_bytes={settings.max_diff_bytes}"
        )
        return 0
    if arguments.command == "serve-slack":
        if settings.slack_owner_user_id is None:
            print("refused: set NEXUS_SOFTWARE_ENGINEER_SLACK_OWNER_USER_ID first", file=sys.stderr)
            return 2
        if not os.environ.get(settings.slack_signing_secret_env, "").strip():
            print(f"refused: {settings.slack_signing_secret_env} is not set", file=sys.stderr)
            return 2
        import uvicorn

        app = create_app(_slack_handler(settings, Path(arguments.state_root)))
        uvicorn.run(app, host=arguments.host, port=arguments.port, log_level="warning")
        return 0
    if arguments.command == "decide":
        try:
            decision = decide(
                state_root=Path(arguments.state_root),
                memory=EngineerMemoryStore(settings.memory_path),
                owner_id=settings.owner_id,
                cycle=arguments.cycle,
                verdict=OwnerVerdict(arguments.verdict),
                reason=arguments.reason,
                now=datetime.now(UTC),
            )
        except ApprovalError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print(
            f"decision={decision.decision_id} request={decision.request_id} "
            f"verdict={decision.verdict.value} by={decision.decided_by.actor_id}"
        )
        return 0
    if arguments.command == "inspect":
        inspector = _inspector(arguments, settings)
        for signal in inspector.collect(arguments.since):
            flags = " [instruction-like]" if signal.instruction_like else ""
            print(f"{signal.severity.value:7} {signal.kind.value:14} {signal.summary}{flags}")
        return 0
    try:
        settings.require_enabled()
    except EngineerDisabledError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if arguments.command == "publish":
        state_root = Path(arguments.state_root)
        try:
            published = publish_approved_change(
                state_root=state_root,
                memory=EngineerMemoryStore(settings.memory_path),
                owner_id=settings.owner_id,
                cycle=arguments.cycle,
                publisher=_publisher(settings),
                git=GitRunner(state_root / "git-runtime"),
                now=datetime.now(UTC),
                allow_moved_base=arguments.allow_moved_base,
            )
        except (ApprovalError, PublishError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print(
            f"published draft pull request #{published.pull_request_number} "
            f"{published.pull_request_url} branch={published.branch} "
            f"remote_commit={published.remote_commit_sha} base={published.base_sha}"
        )
        return 0
    notifier = Notifier(
        _transport(settings),
        clock=lambda: datetime.now(UTC),
        max_per_cycle=settings.max_notifications_per_cycle,
    )
    cycle = EngineeringCycle(
        settings=settings,
        inspector=_inspector(arguments, settings),
        memory=EngineerMemoryStore(settings.memory_path),
        notifier=notifier,
        executor=_executor(settings, arguments),
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
