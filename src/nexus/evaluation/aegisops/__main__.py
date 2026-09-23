"""CLI for live preflight, durable benchmarks, comparison, and baseline locking."""

import argparse
import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

from nexus.aegisops.config import AgentSettings
from nexus.evaluation.aegisops.benchmark import BenchmarkRunner
from nexus.evaluation.aegisops.benchmark_config import BenchmarkSettings
from nexus.evaluation.aegisops.benchmark_models import (
    BenchmarkMode,
    OrderingStrategy,
    RegressionThresholds,
)
from nexus.evaluation.aegisops.comparison import compare_summaries
from nexus.evaluation.aegisops.environment import (
    BenchmarkWarmup,
    DockerComposeStackController,
)
from nexus.evaluation.aegisops.preflight import BenchmarkPreflight
from nexus.evaluation.aegisops.storage import BenchmarkStorage
from nexus.lab.catalog import ScenarioCatalog


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NEXUS AegisOps benchmark tooling")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight", help="verify live benchmark readiness")
    preflight.add_argument("--allow-dirty", action="store_true")
    smoke = commands.add_parser("smoke", help="run one live investigation per scenario")
    _add_live_arguments(smoke, include_runs=False)
    baseline = commands.add_parser("baseline", help="run a repeated live baseline")
    _add_live_arguments(baseline, include_runs=True)
    resume = commands.add_parser("resume", help="resume a persisted benchmark session")
    resume.add_argument("session")
    resume.add_argument("--confirm-live", action="store_true")
    compare = commands.add_parser("compare", help="compare two benchmark summaries")
    compare.add_argument("baseline")
    compare.add_argument("candidate")
    compare.add_argument("--exact-accuracy-decrease", type=float, default=0.05)
    compare.add_argument("--unsupported-claim-increase", type=float, default=0.05)
    compare.add_argument("--unsafe-attempt-increase", type=float, default=0.0)
    compare.add_argument("--tool-call-increase", type=float, default=0.20)
    compare.add_argument("--latency-increase", type=float, default=0.25)
    lock = commands.add_parser("lock", help="accept a genuine completed live baseline")
    lock.add_argument("session")
    lock.add_argument("--name", required=True)
    return parser


def _add_live_arguments(parser: argparse.ArgumentParser, *, include_runs: bool) -> None:
    if include_runs:
        parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument(
        "--ordering",
        choices=[item.value for item in OrderingStrategy],
        default=OrderingStrategy.CATALOG.value,
    )
    parser.add_argument("--seed", type=int)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    benchmark_settings = BenchmarkSettings()
    storage = BenchmarkStorage(benchmark_settings.root)
    if arguments.command == "compare":
        thresholds = RegressionThresholds(
            exact_accuracy_decrease=arguments.exact_accuracy_decrease,
            unsupported_claim_rate_increase=arguments.unsupported_claim_increase,
            unsafe_attempt_rate_increase=arguments.unsafe_attempt_increase,
            average_tool_calls_increase_fraction=arguments.tool_call_increase,
            average_latency_increase_fraction=arguments.latency_increase,
        )
        comparison = compare_summaries(
            storage.load_summary(arguments.baseline),
            storage.load_summary(arguments.candidate),
            thresholds,
        )
        name = (
            f"comparison-{comparison.baseline_session_id}-"
            f"{comparison.candidate_session_id}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        )
        path = storage.write_comparison(name, comparison.model_dump_json(indent=2))
        print(comparison.model_dump_json(indent=2))
        print(f"saved {path}")
        return 0 if comparison.comparable else 2
    if arguments.command == "lock":
        path = storage.lock_baseline(arguments.session, arguments.name)
        print(f"locked {path}")
        return 0

    settings = AgentSettings()
    catalog = ScenarioCatalog.load()
    allow_dirty = bool(getattr(arguments, "allow_dirty", False))
    preflight = BenchmarkPreflight(settings, catalog=catalog).run(allow_dirty=allow_dirty)
    if arguments.command == "preflight":
        print(preflight.model_dump_json(indent=2))
        return 0 if preflight.ready else 2
    if not arguments.confirm_live:
        print("Live benchmark requires --confirm-live; no model calls were made")
        return 2
    if not preflight.ready or preflight.identity is None:
        print(preflight.model_dump_json(indent=2))
        print("Preflight failed; no model calls were made")
        return 2

    runner = BenchmarkRunner(
        settings,
        storage,
        DockerComposeStackController(benchmark_settings.stack_timeout_seconds),
        BenchmarkWarmup(warmup_seconds=benchmark_settings.warmup_seconds),
        catalog=catalog,
    )
    if arguments.command == "resume":
        manifest = storage.load_manifest(arguments.session)
        if manifest.identity != preflight.identity:
            print("Current baseline identity differs from the persisted session")
            return 2
    else:
        runs = 1 if arguments.command == "smoke" else arguments.runs
        mode = BenchmarkMode.SMOKE if arguments.command == "smoke" else BenchmarkMode.BASELINE
        manifest = runner.create_manifest(
            preflight.identity,
            mode,
            runs,
            OrderingStrategy(arguments.ordering),
            arguments.seed,
            benchmark_settings.warmup_seconds,
            allow_dirty,
        )
        storage.create_session(manifest)
    _print_plan(manifest)
    completed = asyncio.run(runner.run(manifest))
    summary = storage.load_summary(completed.benchmark_session_id)
    print(summary.aggregate.model_dump_json(indent=2))
    print(f"session {completed.benchmark_session_id}")
    return 0


def _print_plan(manifest) -> None:  # type: ignore[no-untyped-def]
    identity = manifest.identity
    scenario_count = len({run.scenario_id for run in manifest.actual_run_order})
    print(
        f"model={identity.model} scenarios={scenario_count} "
        f"runs_per_scenario={manifest.runs_per_scenario} "
        f"planned_investigations={manifest.total_planned_runs} "
        f"max_tool_calls={identity.max_tool_calls} max_turns={identity.max_turns} "
        f"timeout={identity.timeout_seconds:g}s git_sha={identity.git_sha} "
        f"working_tree={'dirty' if identity.git_dirty else 'clean'}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
