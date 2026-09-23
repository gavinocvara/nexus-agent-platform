"""Explicit live evaluation command for the AegisOps investigator."""

import argparse
import asyncio
import os
from collections.abc import Sequence

from nexus.aegisops.config import AgentSettings
from nexus.evaluation.aegisops.harness import AegisOpsEvaluationHarness


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live AegisOps scenario evaluation")
    parser.add_argument("--runs", type=int, default=1, help="runs per scenario")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    settings = AgentSettings()
    if not settings.enabled:
        print("Live evaluation disabled; set NEXUS_AGENT_ENABLED=true")
        return 2
    if not os.getenv("OPENAI_API_KEY"):
        print("Live evaluation requires OPENAI_API_KEY")
        return 2
    report = asyncio.run(AegisOpsEvaluationHarness(settings).run(arguments.runs))
    path = AegisOpsEvaluationHarness.save(report)
    print(report.aggregate.model_dump_json(indent=2))
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
