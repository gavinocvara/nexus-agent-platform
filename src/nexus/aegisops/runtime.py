"""Bounded runtime and OpenAI Agents SDK adapter for one investigator."""

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from agents import Agent, RunConfig, Runner
from agents.exceptions import MaxTurnsExceeded

from nexus.aegisops.config import AgentSettings
from nexus.aegisops.context import InvestigatorContext
from nexus.aegisops.instructions import INVESTIGATOR_INSTRUCTIONS
from nexus.aegisops.models import (
    Diagnosis,
    InvestigationRunRecord,
    ModelUsage,
    RunStatus,
)
from nexus.aegisops.output_schema import DIAGNOSIS_OUTPUT_SCHEMA
from nexus.aegisops.tools import build_sdk_tools
from nexus.diagnostics.audit import DiagnosticSession, ToolCallBudgetExceeded
from nexus.diagnostics.service import DiagnosticServiceLayer

GENERIC_INCIDENT_PROMPT = (
    "Investigate the current AegisOps incident using only approved diagnostic evidence. "
    "Identify the most likely affected component and failure class, or abstain when the "
    "available evidence is insufficient."
)


@dataclass(frozen=True, slots=True)
class EngineOutcome:
    diagnosis: Diagnosis
    turn_count: int
    usage: ModelUsage | None = None


class InvestigatorEngine(Protocol):
    async def run(
        self,
        prompt: str,
        context: InvestigatorContext,
        settings: AgentSettings,
    ) -> EngineOutcome: ...


class MissingModelCredentials(RuntimeError):
    """Live execution was requested without an API key."""


class OpenAIAgentsEngine:
    """Narrow adapter around the current OpenAI Agents SDK runner."""

    async def run(
        self,
        prompt: str,
        context: InvestigatorContext,
        settings: AgentSettings,
    ) -> EngineOutcome:
        if not os.getenv("OPENAI_API_KEY"):
            raise MissingModelCredentials(
                "OPENAI_API_KEY is required for live AegisOps investigator execution"
            )
        agent: Agent[InvestigatorContext] = Agent(
            name="aegisops.investigator",
            instructions=INVESTIGATOR_INSTRUCTIONS,
            model=settings.model,
            tools=build_sdk_tools(),
            output_type=DIAGNOSIS_OUTPUT_SCHEMA,
        )
        result = await Runner.run(
            agent,
            prompt,
            context=context,
            max_turns=settings.max_turns,
            run_config=RunConfig(
                tracing_disabled=not settings.sdk_tracing_enabled,
                trace_include_sensitive_data=False,
                workflow_name="NEXUS AegisOps investigation",
            ),
        )
        if not isinstance(result.final_output, Diagnosis):
            raise TypeError("Agent SDK returned an invalid structured diagnosis")
        sdk_usage = result.context_wrapper.usage
        usage = ModelUsage(
            request_count=sdk_usage.requests,
            input_tokens=sdk_usage.input_tokens,
            output_tokens=sdk_usage.output_tokens,
            total_tokens=sdk_usage.total_tokens,
            cached_input_tokens=sdk_usage.input_tokens_details.cached_tokens,
            cache_write_input_tokens=sdk_usage.input_tokens_details.cache_write_tokens,
            reasoning_output_tokens=sdk_usage.output_tokens_details.reasoning_tokens,
        )
        return EngineOutcome(
            diagnosis=result.final_output,
            turn_count=len(result.raw_responses),
            usage=usage,
        )


class InvestigatorRuntime:
    """Own limits, local diagnostic context, failure mapping, and run accounting."""

    def __init__(
        self,
        settings: AgentSettings | None = None,
        engine: InvestigatorEngine | None = None,
    ) -> None:
        self.settings = settings or AgentSettings()
        self.engine = engine or OpenAIAgentsEngine()

    async def investigate(
        self,
        prompt: str = GENERIC_INCIDENT_PROMPT,
        diagnostics: DiagnosticServiceLayer | None = None,
    ) -> InvestigationRunRecord:
        run_id = uuid4()
        started_at = datetime.now(UTC)
        started = perf_counter()
        owned_diagnostics = diagnostics is None
        session = DiagnosticSession(max_tool_calls=self.settings.max_tool_calls)
        service = diagnostics or DiagnosticServiceLayer(session=session)
        if diagnostics is not None:
            session = diagnostics.session
            session.max_tool_calls = self.settings.max_tool_calls
        context = InvestigatorContext(diagnostics=service, session=session, run_id=run_id)
        status = RunStatus.COMPLETED
        diagnosis: Diagnosis | None = None
        usage: ModelUsage | None = None
        turn_count = 0
        error: str | None = None

        try:
            if not self.settings.enabled:
                status = RunStatus.DISABLED
                error = "AegisOps investigator is disabled; set NEXUS_AGENT_ENABLED=true"
            else:
                outcome = await asyncio.wait_for(
                    self.engine.run(prompt, context, self.settings),
                    timeout=self.settings.timeout_seconds,
                )
                diagnosis = outcome.diagnosis
                usage = outcome.usage
                turn_count = outcome.turn_count
        except MissingModelCredentials as exc:
            status = RunStatus.MISSING_CREDENTIALS
            error = str(exc)
        except ToolCallBudgetExceeded as exc:
            status = RunStatus.TOOL_BUDGET_EXCEEDED
            error = str(exc)
        except MaxTurnsExceeded:
            status = RunStatus.MAX_TURNS_EXCEEDED
            error = f"Agent exceeded the {self.settings.max_turns}-turn limit"
        except TimeoutError:
            status = RunStatus.TIMED_OUT
            error = f"Agent exceeded the {self.settings.timeout_seconds:g}-second timeout"
        except (TypeError, ValueError) as exc:
            status = RunStatus.INVALID_OUTPUT
            error = f"Structured diagnosis was invalid ({type(exc).__name__})"
        except Exception:
            status = RunStatus.MODEL_ERROR
            error = "Model execution failed"
        finally:
            if owned_diagnostics:
                service.close()

        finished_at = datetime.now(UTC)
        return InvestigationRunRecord(
            run_id=run_id,
            model=self.settings.model,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(perf_counter() - started) * 1000,
            tool_call_count=session.tool_call_count,
            turn_count=turn_count,
            diagnostic_session_id=session.session_id,
            diagnosis=diagnosis,
            usage=usage,
            error=error,
        )
