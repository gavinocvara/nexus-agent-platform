"""Bounded runtime and OpenAI Agents SDK adapter for one investigator."""

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol
from uuid import uuid4

import openai
from agents import Agent, RunConfig, RunContextWrapper, Runner
from agents.exceptions import (
    AgentsException,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelTimeoutError,
)
from agents.items import ModelResponse
from agents.lifecycle import RunHooksBase
from agents.usage import Usage

from nexus.aegisops.config import AgentSettings
from nexus.aegisops.context import InvestigatorContext
from nexus.aegisops.instructions import INVESTIGATOR_INSTRUCTIONS
from nexus.aegisops.models import (
    Diagnosis,
    FailureCategory,
    FailureOrigin,
    InvestigationRunRecord,
    ModelUsage,
    RunFailure,
    RunStatus,
)
from nexus.aegisops.output_schema import DIAGNOSIS_OUTPUT_SCHEMA, DiagnosisOutputValidationError
from nexus.aegisops.tools import DiagnosticToolExecutionError, ToolBoundaryError, build_sdk_tools
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


class EngineExecutionError(RuntimeError):
    """Preserve partial accounting while retaining the original exception as the cause."""

    def __init__(self, turn_count: int | None, usage: ModelUsage | None) -> None:
        super().__init__("Agent engine execution failed")
        self.turn_count = turn_count
        self.usage = usage


class EngineOutputContractError(TypeError):
    """The SDK returned a final value outside the configured Diagnosis contract."""


class _AccountingHooks(RunHooksBase[InvestigatorContext, Agent[InvestigatorContext]]):
    def __init__(self) -> None:
        self.turn_count = 0
        self.usage: ModelUsage | None = None

    async def on_llm_end(
        self,
        context: RunContextWrapper[InvestigatorContext],
        agent: Agent[InvestigatorContext],
        response: ModelResponse,
    ) -> None:
        del agent, response
        self.turn_count += 1
        self.usage = _model_usage(context.usage)


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
        accounting = _AccountingHooks()
        try:
            result = await Runner.run(
                agent,
                prompt,
                context=context,
                max_turns=settings.max_turns,
                hooks=accounting,
                run_config=RunConfig(
                    tracing_disabled=not settings.sdk_tracing_enabled,
                    trace_include_sensitive_data=False,
                    workflow_name="NEXUS AegisOps investigation",
                ),
            )
        except Exception as exc:
            turn_count, usage = _partial_accounting(exc, accounting)
            raise EngineExecutionError(turn_count, usage) from exc
        if not isinstance(result.final_output, Diagnosis):
            raise EngineOutputContractError("Agent SDK returned an invalid structured diagnosis")
        usage = _model_usage(result.context_wrapper.usage)
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
        turn_count: int | None = None
        accounting_complete = False
        error: str | None = None
        failure: RunFailure | None = None

        try:
            if not self.settings.enabled:
                status = RunStatus.DISABLED
                error = "AegisOps investigator is disabled; set NEXUS_AGENT_ENABLED=true"
                turn_count = 0
                accounting_complete = True
            else:
                outcome = await asyncio.wait_for(
                    self.engine.run(prompt, context, self.settings),
                    timeout=self.settings.timeout_seconds,
                )
                diagnosis = outcome.diagnosis
                usage = outcome.usage
                turn_count = outcome.turn_count
                accounting_complete = True
        except Exception as exc:
            status, error, failure = _classify_failure(exc, self.settings)
            if isinstance(exc, EngineExecutionError):
                turn_count = exc.turn_count
                usage = exc.usage
            elif status is RunStatus.MISSING_CREDENTIALS:
                turn_count = 0
                accounting_complete = True
        finally:
            await asyncio.to_thread(session.wait_for_idle, min(self.settings.timeout_seconds, 15))
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
            accounting_complete=accounting_complete,
            diagnostic_session_id=session.session_id,
            diagnosis=diagnosis,
            usage=usage,
            failure=failure,
            error=error,
        )


def _model_usage(usage: Usage) -> ModelUsage:
    return ModelUsage(
        request_count=usage.requests,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cached_input_tokens=usage.input_tokens_details.cached_tokens,
        cache_write_input_tokens=usage.input_tokens_details.cache_write_tokens,
        reasoning_output_tokens=usage.output_tokens_details.reasoning_tokens,
    )


def _partial_accounting(
    error: Exception, hooks: _AccountingHooks
) -> tuple[int | None, ModelUsage | None]:
    turn_count = hooks.turn_count
    usage = hooks.usage
    if isinstance(error, AgentsException) and error.run_data is not None:
        turn_count = max(turn_count, len(error.run_data.raw_responses))
        usage = _model_usage(error.run_data.context_wrapper.usage)
    return turn_count, usage


def _exception_graph(error: BaseException) -> list[BaseException]:
    pending = [error]
    found: list[BaseException] = []
    seen: set[int] = set()
    while pending and len(found) < 12:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif current.__context__ is not None:
            pending.append(current.__context__)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
    return found


def _qualified_type(error: BaseException) -> str:
    error_type = type(error)
    return f"{error_type.__module__}.{error_type.__qualname__}"


def _provider_metadata(errors: list[BaseException]) -> tuple[int | None, str | None]:
    for error in errors:
        if not isinstance(error, openai.APIError):
            continue
        status = getattr(error, "status_code", None)
        status_code = status if isinstance(status, int) and 100 <= status <= 599 else None
        raw_code: Any = getattr(error, "code", None)
        code = raw_code if isinstance(raw_code, str) else None
        if code is not None and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", code) is None:
            code = None
        return status_code, code
    return None, None


def _classify_failure(
    error: Exception, settings: AgentSettings
) -> tuple[RunStatus, str, RunFailure]:
    errors = _exception_graph(error)
    status = RunStatus.MODEL_ERROR
    category = FailureCategory.INTERNAL
    origin = FailureOrigin.INTERNAL_RUNTIME
    message = "Internal investigator runtime failure"
    if any(isinstance(item, MissingModelCredentials) for item in errors):
        status = RunStatus.MISSING_CREDENTIALS
        category = FailureCategory.CREDENTIALS
        origin = FailureOrigin.CONFIGURATION
        message = "Live model credentials are unavailable"
    elif any(isinstance(item, ToolCallBudgetExceeded) for item in errors):
        status = RunStatus.TOOL_BUDGET_EXCEEDED
        category = FailureCategory.TOOL_BUDGET
        origin = FailureOrigin.BUDGET_ENFORCEMENT
        message = f"Diagnostic tool-call budget of {settings.max_tool_calls} was exhausted"
    elif any(isinstance(item, MaxTurnsExceeded) for item in errors):
        status = RunStatus.MAX_TURNS_EXCEEDED
        category = FailureCategory.TURN_LIMIT
        origin = FailureOrigin.SDK_RUNTIME
        message = f"Agent exceeded the {settings.max_turns}-turn limit"
    elif any(isinstance(item, TimeoutError | ModelTimeoutError) for item in errors):
        status = RunStatus.TIMED_OUT
        category = FailureCategory.TIMEOUT
        origin = FailureOrigin.PROVIDER_TRANSPORT
        message = f"Agent exceeded the {settings.timeout_seconds:g}-second timeout"
    elif any(
        isinstance(item, DiagnosisOutputValidationError | EngineOutputContractError)
        for item in errors
    ):
        status = RunStatus.INVALID_OUTPUT
        category = FailureCategory.OUTPUT_VALIDATION
        origin = FailureOrigin.FINAL_OUTPUT_VALIDATION
        message = "Structured diagnosis failed domain validation"
    elif any(isinstance(item, ModelBehaviorError) for item in errors):
        category = FailureCategory.MODEL_BEHAVIOR
        origin = FailureOrigin.SDK_RUNTIME
        message = "Model behavior violated the agent SDK contract"
    elif any(isinstance(item, DiagnosticToolExecutionError | ToolBoundaryError) for item in errors):
        category = FailureCategory.TOOL_EXECUTION
        origin = FailureOrigin.TOOL_EXECUTION
        message = "Diagnostic tool execution failed"
    elif any(isinstance(item, openai.APIError) for item in errors):
        category = FailureCategory.PROVIDER
        origin = FailureOrigin.PROVIDER_TRANSPORT
        message = "Model provider request failed"
    elif any(isinstance(item, AgentsException) for item in errors):
        category = FailureCategory.SDK
        origin = FailureOrigin.SDK_RUNTIME
        message = "Agent SDK execution failed"
    provider_status, provider_code = _provider_metadata(errors)
    failure = RunFailure(
        category=category,
        origin=origin,
        outer_exception_type=_qualified_type(error),
        cause_chain_types=[_qualified_type(item) for item in errors[1:]],
        provider_status_code=provider_status,
        provider_error_code=provider_code,
    )
    return status, message, failure
