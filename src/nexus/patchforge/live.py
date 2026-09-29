"""Explicitly authorized, single-task live PatchForge run (Milestone I).

A live run needs all three of: ``NEXUS_PATCHFORGE_LIVE_ENABLED=true``, an
``OPENAI_API_KEY`` in the (ignored) environment, and ``--confirm-live``. It runs exactly
one Benchmark v0 task through the real PatchForge path with a model-backed engine and the
content-oracle sandbox, so no repository code executes and nothing leaves the machine
except the model requests. It writes its artifacts and stops.

``preflight`` makes no network request and never prints the key, only whether it exists.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from nexus.patchforge.benchmark import (
    BenchmarkTask,
    TaskScore,
    _oracle_factory,
    evaluate,
)
from nexus.patchforge.benchmark_corpus import default_corpus
from nexus.patchforge.canonical import canonical_json
from nexus.patchforge.e2e import E2ERun, E2EScenario, PatchForgeE2EHarness
from nexus.patchforge.e2e_catalog import calculator_budgets
from nexus.patchforge.engine import (
    ENGINE_PROMPT_VERSION,
    EngineBudget,
    ModelBackedEngine,
    ModelClient,
    ModelRequest,
    ModelResponse,
)
from nexus.patchforge.models import PatchOutcome
from nexus.patchforge.workspace import GitRunner

API_KEY_VARIABLE = "OPENAI_API_KEY"


class LiveSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEXUS_PATCHFORGE_LIVE_", extra="ignore")

    enabled: bool = False
    model: str = Field(default="gpt-5.6-sol", min_length=1, max_length=100)
    max_model_calls: int = Field(default=40, ge=1, le=200)
    max_output_tokens: int = Field(default=1024, ge=64, le=32_000)
    timeout_seconds: float = Field(default=60, gt=0, le=600)


class LiveAuthorizationError(RuntimeError):
    """A live run was requested without every explicit authorization."""


@dataclass(frozen=True, slots=True)
class Readiness:
    enabled: bool
    api_key_present: bool
    model: str

    @property
    def ready(self) -> bool:
        return self.enabled and self.api_key_present


def preflight(settings: LiveSettings) -> Readiness:
    """Report readiness without any network request; the key's value is never read out."""

    return Readiness(
        enabled=settings.enabled,
        api_key_present=bool(os.environ.get(API_KEY_VARIABLE, "").strip()),
        model=settings.model,
    )


def require_authorization(settings: LiveSettings, *, confirmed: bool) -> None:
    readiness = preflight(settings)
    if not confirmed:
        raise LiveAuthorizationError("A live run requires --confirm-live")
    if not readiness.enabled:
        raise LiveAuthorizationError("Set NEXUS_PATCHFORGE_LIVE_ENABLED=true to allow live runs")
    if not readiness.api_key_present:
        raise LiveAuthorizationError(f"{API_KEY_VARIABLE} is not set in the environment")


class OpenAIResponsesClient:
    """``ModelClient`` over the OpenAI Responses API with a strict JSON-schema reply."""

    def __init__(self, client: Any) -> None:
        # An ``openai.OpenAI`` instance, or a test double exposing ``responses.create``.
        self._client = client

    @classmethod
    def from_environment(cls, settings: LiveSettings) -> OpenAIResponsesClient:
        import openai

        # The SDK reads the key from the environment; it is never passed through NEXUS code.
        return cls(openai.OpenAI(timeout=settings.timeout_seconds, max_retries=0))

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self._client.responses.create(
            model=request.model,
            input=[
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            max_output_tokens=request.max_output_tokens,
            text={"format": action_format(request)},
        )
        usage = getattr(response, "usage", None)
        return ModelResponse(
            text=str(response.output_text),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )


def action_format(request: ModelRequest) -> dict[str, Any]:
    """Strict structured-output schema: one permitted tool and its JSON-encoded arguments."""

    return {
        "type": "json_schema",
        "name": "patchforge_action",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "enum": [tool.value for tool in request.tool_names]},
                "arguments_json": {"type": "string"},
            },
            "required": ["tool", "arguments_json"],
            "additionalProperties": False,
        },
    }


@dataclass(frozen=True, slots=True)
class LiveRun:
    run: E2ERun
    score: TaskScore
    engine: ModelBackedEngine


def run_live_task(
    task: BenchmarkTask,
    client: ModelClient,
    settings: LiveSettings,
    work_root: Path,
    *,
    now: datetime | None = None,
) -> LiveRun:
    """Run one task through the real path with a model-backed engine; no retries."""

    engine = ModelBackedEngine(
        client=client,
        model=settings.model,
        budget=EngineBudget(
            max_model_calls=settings.max_model_calls,
            max_output_tokens=settings.max_output_tokens,
            max_rendered_result_chars=8000,
            max_history_turns=12,
        ),
    )
    scenario = E2EScenario(
        name=task.name,
        fixture=task.fixture,
        profile=task.profile,
        budgets=calculator_budgets(),
        steps=(),
        sandbox_plans=(),
        expected_outcome=PatchOutcome.PATCH_PROPOSED,
        expected_failure=None,
        title=task.title,
        instructions=task.instructions,
        acceptance_criteria=tuple(task.acceptance_criteria),
        sandbox_factory=_oracle_factory(task.truth),
        engine_factory=lambda worktree: engine,
        engine_kind="model",
        engine_version=f"{settings.model}/{ENGINE_PROMPT_VERSION}"[:100],
    )
    started = now or datetime.now(UTC)
    run = PatchForgeE2EHarness(work_root / "harness", now=started).run(scenario)
    evaluation = work_root / "evaluation"
    evaluation.mkdir(parents=True)
    score = evaluate(task, run, evaluation, GitRunner(work_root / "evaluator-git"), now=started)
    return LiveRun(run=run, score=score, engine=engine)


def write_live_artifacts(live: LiveRun, output: Path) -> Path:
    """Persist the attested result, independent score, and engine call audit."""

    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{live.run.scenario.name}.json"
    payload = {
        "engine_prompt_version": ENGINE_PROMPT_VERSION,
        "result": json.loads(canonical_json(live.run.result)),
        "score": live.score.model_dump(mode="json"),
        "engine_calls": [item.model_dump(mode="json") for item in live.engine.records],
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[LiveSettings], ModelClient] = OpenAIResponsesClient.from_environment,
) -> int:
    parser = argparse.ArgumentParser(description="Explicitly authorized live PatchForge run.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight", help="report readiness without any network request")
    run_parser = commands.add_parser("run", help="run exactly one benchmark task live")
    run_parser.add_argument("--task", required=True, choices=[t.name for t in default_corpus()])
    run_parser.add_argument("--confirm-live", action="store_true")
    run_parser.add_argument("--output", type=Path, default=Path(".nexus/patchforge/live"))
    arguments = parser.parse_args(argv)
    settings = LiveSettings()
    if arguments.command == "preflight":
        readiness = preflight(settings)
        print(
            f"enabled={readiness.enabled} api_key_present={readiness.api_key_present} "
            f"model={readiness.model} ready={readiness.ready}"
        )
        return 0 if readiness.ready else 1
    try:
        require_authorization(settings, confirmed=arguments.confirm_live)
    except LiveAuthorizationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    task = next(item for item in default_corpus() if item.name == arguments.task)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    work = arguments.output / "work" / f"{task.name}-{stamp}"
    live = run_live_task(task, client_factory(settings), settings, work)
    artifact = write_live_artifacts(live, arguments.output)
    verdict = live.score.sentinel_verdict.value if live.score.sentinel_verdict else "not_reviewed"
    print(
        f"task={task.name} outcome={live.score.outcome.value} resolved={live.score.resolved} "
        f"false_proposal={live.score.false_proposal} sentinelqa={verdict} "
        f"model_calls={len(live.engine.records)} "
        f"invariant_violations={len(live.score.invariant_violations)} artifact={artifact}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
