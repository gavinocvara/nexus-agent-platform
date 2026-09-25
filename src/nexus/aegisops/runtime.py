"""Bounded runtime and OpenAI Agents SDK adapter for one investigator."""

import asyncio
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from time import perf_counter
from typing import Any, Protocol
from uuid import uuid4

import openai
from agents import Agent, RunConfig, RunContextWrapper, Runner, Tool
from agents.exceptions import (
    AgentsException,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelTimeoutError,
)
from agents.items import ModelResponse, ToolCallOutputItem
from agents.lifecycle import RunHooksBase
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall
from pydantic import ValidationError

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
    SafeValidationError,
    SdkValidationPhase,
)
from nexus.aegisops.output_schema import DIAGNOSIS_OUTPUT_SCHEMA, DiagnosisOutputValidationError
from nexus.aegisops.tools import (
    DiagnosticToolExecutionError,
    ToolBoundaryError,
    build_sdk_tools,
    reset_tool_body_observer,
    set_tool_body_observer,
)
from nexus.brain.config import BrainSettings
from nexus.brain.manager import AegisOpsBrain, BrainFailure
from nexus.brain.models import (
    AgentObservableRun,
    BrainMode,
    BrainRunMetadata,
    InvestigationCompletion,
    ObservableToolCall,
    SelfReportedDiagnosis,
)
from nexus.diagnostics.audit import DiagnosticAuditEvent, DiagnosticSession, ToolCallBudgetExceeded
from nexus.diagnostics.service import DiagnosticServiceLayer

GENERIC_INCIDENT_PROMPT = (
    "Investigate the current AegisOps incident using only approved diagnostic evidence. "
    "Identify the most likely affected component and failure class, or abstain when the "
    "available evidence is insufficient."
)
_SAFE_VALIDATION_LOCATIONS = frozenset(
    {
        "service",
        "dependency",
        "window",
        "level",
        "correlation_id",
        "status_code",
        "limit",
        "trace_id",
        "tool",
        "source",
        "success",
        "events",
        "trace_ids",
        "trace",
    }
)


@dataclass(frozen=True, slots=True)
class EngineOutcome:
    diagnosis: Diagnosis
    turn_count: int
    usage: ModelUsage | None = None


class EngineExecutionError(RuntimeError):
    """Preserve partial accounting while retaining the original exception as the cause."""

    def __init__(
        self,
        turn_count: int | None,
        usage: ModelUsage | None,
        sdk_failure: "_SdkFailureDetails | None" = None,
    ) -> None:
        super().__init__("Agent engine execution failed")
        self.turn_count = turn_count
        self.usage = usage
        self.sdk_failure = sdk_failure


class EngineOutputContractError(TypeError):
    """The SDK returned a final value outside the configured Diagnosis contract."""


@dataclass(slots=True)
class _ToolLifecycle:
    name: str
    invocation_began: bool = False
    body_invoked: bool = False
    output_produced: bool = False


@dataclass(frozen=True, slots=True)
class _SdkFailureDetails:
    phase: SdkValidationPhase
    validation_errors: tuple[SafeValidationError, ...]
    tool_name: str | None
    function_call_position: int | None
    invocation_began: bool | None
    tool_body_invoked: bool | None
    tool_output_produced: bool | None


class _AccountingHooks(RunHooksBase[InvestigatorContext, Agent[InvestigatorContext]]):
    def __init__(self) -> None:
        self.turn_count = 0
        self.usage: ModelUsage | None = None
        self._tool_calls: dict[str, _ToolLifecycle] = {}
        self._lock = Lock()

    async def on_llm_end(
        self,
        context: RunContextWrapper[InvestigatorContext],
        agent: Agent[InvestigatorContext],
        response: ModelResponse,
    ) -> None:
        del agent, response
        self.turn_count += 1
        self.usage = _model_usage(context.usage)

    async def on_tool_start(
        self,
        context: RunContextWrapper[InvestigatorContext],
        agent: Agent[InvestigatorContext],
        tool: Tool,
    ) -> None:
        del agent
        call_id = getattr(context, "tool_call_id", None)
        if not isinstance(call_id, str):
            return
        with self._lock:
            self._tool_calls[call_id] = _ToolLifecycle(name=tool.name, invocation_began=True)

    async def on_tool_end(
        self,
        context: RunContextWrapper[InvestigatorContext],
        agent: Agent[InvestigatorContext],
        tool: Tool,
        result: object,
    ) -> None:
        del agent, tool, result
        call_id = getattr(context, "tool_call_id", None)
        if not isinstance(call_id, str):
            return
        with self._lock:
            lifecycle = self._tool_calls.get(call_id)
            if lifecycle is not None:
                lifecycle.output_produced = True

    def mark_tool_body(self, tool_name: str, call_id: str | None) -> None:
        if call_id is None:
            return
        with self._lock:
            lifecycle = self._tool_calls.setdefault(call_id, _ToolLifecycle(name=tool_name))
            lifecycle.body_invoked = True

    def tool_calls(self) -> dict[str, _ToolLifecycle]:
        with self._lock:
            return {
                call_id: _ToolLifecycle(
                    name=value.name,
                    invocation_began=value.invocation_began,
                    body_invoked=value.body_invoked,
                    output_produced=value.output_produced,
                )
                for call_id, value in self._tool_calls.items()
            }


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
        observer_token = set_tool_body_observer(accounting.mark_tool_body)
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
            sdk_failure = _sdk_failure_details(exc, accounting)
            raise EngineExecutionError(turn_count, usage, sdk_failure) from exc
        finally:
            reset_tool_body_observer(observer_token)
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
        brain_settings: BrainSettings | None = None,
        brain: AegisOpsBrain | None = None,
    ) -> None:
        self.settings = settings or AgentSettings()
        self.engine = engine or OpenAIAgentsEngine()
        explicit_brain_settings = brain_settings or BrainSettings(mode=BrainMode.DISABLED)
        self.brain = brain or AegisOpsBrain(explicit_brain_settings)

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
        brain_metadata = BrainRunMetadata()
        retrieval_prompt = prompt
        agent_loop_started: float | None = None

        try:
            if not self.settings.enabled:
                status = RunStatus.DISABLED
                error = "AegisOps investigator is disabled; set NEXUS_AGENT_ENABLED=true"
                turn_count = 0
                accounting_complete = True
            else:
                retrieval = await self.brain.retrieve(prompt, as_of=started_at)
                retrieval_prompt = retrieval.prompt
                brain_metadata = retrieval.metadata
                agent_loop_started = perf_counter()
                outcome = await asyncio.wait_for(
                    self.engine.run(retrieval_prompt, context, self.settings),
                    timeout=self.settings.timeout_seconds,
                )
                diagnosis = outcome.diagnosis
                usage = outcome.usage
                turn_count = outcome.turn_count
                accounting_complete = True
        except Exception as exc:
            status, error, failure = _classify_failure(exc, self.settings)
            if isinstance(exc, BrainFailure):
                brain_metadata = exc.metadata
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
        agent_loop_duration_ms = (
            (perf_counter() - agent_loop_started) * 1000 if agent_loop_started is not None else None
        )
        record = InvestigationRunRecord(
            run_id=run_id,
            model=self.settings.model,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(
                agent_loop_duration_ms
                if agent_loop_duration_ms is not None
                else (perf_counter() - started) * 1000
            ),
            agent_loop_duration_ms=agent_loop_duration_ms,
            end_to_end_duration_ms=(perf_counter() - started) * 1000,
            tool_call_count=session.tool_call_count,
            turn_count=turn_count,
            accounting_complete=accounting_complete,
            diagnostic_session_id=session.session_id,
            diagnosis=diagnosis,
            usage=usage,
            failure=failure,
            brain=brain_metadata,
            error=error,
        )
        if status in {RunStatus.DISABLED, RunStatus.BRAIN_FAILURE}:
            return record.model_copy(
                update={"end_to_end_duration_ms": (perf_counter() - started) * 1000}
            )
        try:
            if status is RunStatus.MISSING_CREDENTIALS:
                if self.brain.settings.mode is BrainMode.FROZEN_EVAL:
                    brain_metadata = self.brain.finish_frozen_run(brain_metadata)
                return record.model_copy(
                    update={
                        "brain": brain_metadata,
                        "end_to_end_duration_ms": (perf_counter() - started) * 1000,
                    }
                )
            if self.brain.settings.mode is BrainMode.LEARN:
                projection = _observable_projection(record, session.audit_records())
                brain_metadata = self.brain.write_experience(projection, brain_metadata)
            elif self.brain.settings.mode is BrainMode.FROZEN_EVAL:
                brain_metadata = self.brain.finish_frozen_run(brain_metadata)
            return record.model_copy(
                update={
                    "brain": brain_metadata,
                    "end_to_end_duration_ms": (perf_counter() - started) * 1000,
                }
            )
        except BrainFailure as exc:
            failed_status, failed_error, failed_details = _classify_failure(exc, self.settings)
            return record.model_copy(
                update={
                    "status": failed_status,
                    "brain": exc.metadata,
                    "failure": failed_details,
                    "error": failed_error,
                    "end_to_end_duration_ms": (perf_counter() - started) * 1000,
                }
            )


def _observable_projection(
    record: InvestigationRunRecord,
    events: Sequence[DiagnosticAuditEvent],
) -> AgentObservableRun:
    """Copy the approved writer fields explicitly from agent-owned runtime state."""

    diagnosis = record.diagnosis
    hypothesis = diagnosis.primary_hypothesis if diagnosis is not None else None
    current_tool_call_ids = {event.tool_call_id for event in events}
    self_report = (
        SelfReportedDiagnosis(
            status=diagnosis.status.value,
            component=hypothesis.component.value if hypothesis is not None else None,
            failure_class=(hypothesis.failure_class.value if hypothesis is not None else None),
            confidence=diagnosis.confidence,
            evidence_tool_call_ids=[
                reference.tool_call_id
                for reference in [
                    *diagnosis.supporting_evidence,
                    *diagnosis.conflicting_evidence,
                ]
                if reference.tool_call_id in current_tool_call_ids
            ],
        )
        if diagnosis is not None
        else None
    )
    return AgentObservableRun(
        agent_run_id=record.run_id,
        started_at=record.started_at,
        finished_at=record.finished_at,
        runtime_outcome=_brain_completion(record),
        diagnosis=self_report,
        tool_calls=[
            ObservableToolCall(
                tool_call_id=event.tool_call_id,
                tool=event.tool,
                success=event.success,
                backend=event.backend.value,
                backend_error_code=(
                    event.backend_error_code.value if event.backend_error_code is not None else None
                ),
                backend_status_code=event.backend_status_code,
                result_count=event.result_count,
            )
            for event in events
        ],
        turn_count=record.turn_count,
        input_tokens=record.usage.input_tokens if record.usage is not None else None,
        output_tokens=record.usage.output_tokens if record.usage is not None else None,
    )


def _brain_completion(record: InvestigationRunRecord) -> InvestigationCompletion:
    if record.status is RunStatus.COMPLETED:
        if (
            record.diagnosis is not None
            and record.diagnosis.status.value == "diagnostic_backend_failure"
        ):
            return InvestigationCompletion.BACKEND_FAILURE
        return InvestigationCompletion.COMPLETED
    mapping = {
        RunStatus.TOOL_BUDGET_EXCEEDED: InvestigationCompletion.TOOL_BUDGET_EXHAUSTED,
        RunStatus.MAX_TURNS_EXCEEDED: InvestigationCompletion.TURN_LIMIT_EXHAUSTED,
        RunStatus.TIMED_OUT: InvestigationCompletion.TIMED_OUT,
        RunStatus.INVALID_OUTPUT: InvestigationCompletion.INVALID_OUTPUT,
    }
    return mapping.get(record.status, InvestigationCompletion.FAILED)


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


def _traceback_frames(error: BaseException) -> set[tuple[str, str]]:
    frames: set[tuple[str, str]] = set()
    traceback = error.__traceback__
    while traceback is not None:
        module = traceback.tb_frame.f_globals.get("__name__")
        if isinstance(module, str):
            frames.add((module, traceback.tb_frame.f_code.co_name))
        traceback = traceback.tb_next
    return frames


def _validation_phase(errors: list[BaseException]) -> SdkValidationPhase:
    frames = {frame for error in errors for frame in _traceback_frames(error)}
    if any(
        module == "agents.tool"
        and function in {"_prepare_arguments", "_parse_function_tool_json_input"}
        for module, function in frames
    ):
        return SdkValidationPhase.TOOL_INPUT
    if any(
        (module == "agents.tool" and function == "_validate_function_tool_output")
        or (module == "agents.items" and function == "tool_call_output_item")
        for module, function in frames
    ):
        return SdkValidationPhase.TOOL_OUTPUT
    return SdkValidationPhase.SDK_RUN_ITEM


def _safe_validation_errors(errors: list[BaseException]) -> tuple[SafeValidationError, ...]:
    validation = next((item for item in errors if isinstance(item, ValidationError)), None)
    if validation is None:
        return ()
    safe: list[SafeValidationError] = []
    for item in validation.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:20]:
        location: list[str | int] = []
        for part in item.get("loc", ())[:20]:
            if isinstance(part, int):
                location.append(part)
            elif isinstance(part, str) and part in _SAFE_VALIDATION_LOCATIONS:
                location.append(part)
            else:
                location.append("unrecognized_field")
        error_type = item.get("type")
        if not isinstance(error_type, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,100}", error_type
        ):
            error_type = "unknown"
        safe.append(SafeValidationError(location=location, error_type=error_type))
    return tuple(safe)


def _run_data_calls(error: Exception) -> tuple[list[tuple[str, str, int]], set[str]]:
    if not isinstance(error, AgentsException) or error.run_data is None:
        return [], set()
    calls: list[tuple[str, str, int]] = []
    for response in error.run_data.raw_responses:
        for item in response.output:
            if isinstance(item, ResponseFunctionToolCall):
                calls.append((item.call_id, item.name, len(calls) + 1))
    output_call_ids: set[str] = set()
    for run_item in error.run_data.new_items:
        if not isinstance(run_item, ToolCallOutputItem):
            continue
        raw_item = run_item.raw_item
        raw_call_id = (
            raw_item.get("call_id")
            if isinstance(raw_item, Mapping)
            else getattr(raw_item, "call_id", None)
        )
        if isinstance(raw_call_id, str):
            output_call_ids.add(raw_call_id)
    return calls, output_call_ids


def _sdk_failure_details(error: Exception, hooks: _AccountingHooks) -> _SdkFailureDetails | None:
    errors = _exception_graph(error)
    validation_errors = _safe_validation_errors(errors)
    if not validation_errors:
        return None
    phase = _validation_phase(errors)
    calls, run_data_outputs = _run_data_calls(error)
    lifecycles = hooks.tool_calls()
    candidates = [
        (call_id, name, position)
        for call_id, name, position in calls
        if call_id in lifecycles
        and not (lifecycles[call_id].output_produced or call_id in run_data_outputs)
    ]
    candidate: tuple[str, str, int | None] | None = candidates[0] if len(candidates) == 1 else None
    if candidate is None:
        pending = [
            (call_id, lifecycle)
            for call_id, lifecycle in lifecycles.items()
            if not lifecycle.output_produced and call_id not in run_data_outputs
        ]
        if len(pending) == 1:
            call_id, lifecycle = pending[0]
            position = next((p for cid, _name, p in calls if cid == call_id), None)
            candidate = (call_id, lifecycle.name, position)
    if candidate is None:
        return _SdkFailureDetails(
            phase=phase,
            validation_errors=validation_errors,
            tool_name=None,
            function_call_position=None,
            invocation_began=None,
            tool_body_invoked=None,
            tool_output_produced=None,
        )
    call_id, tool_name, position = candidate
    lifecycle = lifecycles[call_id]
    return _SdkFailureDetails(
        phase=phase,
        validation_errors=validation_errors,
        tool_name=tool_name,
        function_call_position=position,
        invocation_began=lifecycle.invocation_began,
        tool_body_invoked=lifecycle.body_invoked,
        tool_output_produced=lifecycle.output_produced or call_id in run_data_outputs,
    )


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
    sdk_failure = error.sdk_failure if isinstance(error, EngineExecutionError) else None
    status = RunStatus.MODEL_ERROR
    category = FailureCategory.INTERNAL
    origin = FailureOrigin.INTERNAL_RUNTIME
    message = "Internal investigator runtime failure"
    if any(isinstance(item, MissingModelCredentials) for item in errors):
        status = RunStatus.MISSING_CREDENTIALS
        category = FailureCategory.CREDENTIALS
        origin = FailureOrigin.CONFIGURATION
        message = "Live model credentials are unavailable"
    elif any(isinstance(item, BrainFailure) for item in errors):
        status = RunStatus.BRAIN_FAILURE
        category = FailureCategory.BRAIN
        origin = FailureOrigin.PRIVATE_MEMORY
        message = "Private Brain operation failed"
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
    elif sdk_failure is not None and sdk_failure.phase is SdkValidationPhase.TOOL_INPUT:
        category = FailureCategory.TOOL_INPUT_VALIDATION
        origin = FailureOrigin.TOOL_INPUT_VALIDATION
        message = "Function-tool input failed SDK validation"
    elif sdk_failure is not None and sdk_failure.phase is SdkValidationPhase.TOOL_OUTPUT:
        category = FailureCategory.TOOL_OUTPUT_VALIDATION
        origin = FailureOrigin.TOOL_OUTPUT_VALIDATION
        message = "Function-tool output failed SDK validation"
    elif sdk_failure is not None:
        category = FailureCategory.SDK_RUN_ITEM_VALIDATION
        origin = FailureOrigin.SDK_RUN_ITEM_VALIDATION
        message = "Agent SDK run item failed validation"
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
        sdk_validation_phase=sdk_failure.phase if sdk_failure is not None else None,
        validation_errors=(list(sdk_failure.validation_errors) if sdk_failure is not None else []),
        tool_name=sdk_failure.tool_name if sdk_failure is not None else None,
        function_call_position=(
            sdk_failure.function_call_position if sdk_failure is not None else None
        ),
        invocation_began=sdk_failure.invocation_began if sdk_failure is not None else None,
        tool_body_invoked=sdk_failure.tool_body_invoked if sdk_failure is not None else None,
        tool_output_produced=(
            sdk_failure.tool_output_produced if sdk_failure is not None else None
        ),
    )
    return status, message, failure
