"""In-memory diagnostic call accounting and safe audit metadata."""

from datetime import UTC, datetime
from threading import Condition, RLock, get_ident
from time import monotonic
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from nexus.diagnostics.models import (
    DiagnosticBackendErrorCode,
    DiagnosticResult,
    DiagnosticSource,
)


class DiagnosticAuditEvent(BaseModel):
    """One tool execution record without returned evidence or secrets."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    tool_call_id: UUID
    tool: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    normalized_arguments: dict[str, JsonValue]
    success: bool
    backend_error_code: DiagnosticBackendErrorCode | None = None
    backend_status_code: int | None = Field(default=None, ge=100, le=599)
    duration_ms: float = Field(ge=0)
    backend: DiagnosticSource
    result_count: int = Field(ge=0)


class DiagnosticSession:
    """Track tool usage now so later agent evaluation can impose budgets."""

    def __init__(self, session_id: UUID | None = None, max_tool_calls: int | None = None) -> None:
        self.session_id = session_id or uuid4()
        self.max_tool_calls = max_tool_calls
        self._events: list[DiagnosticAuditEvent] = []
        self._results: dict[UUID, DiagnosticResult] = {}
        self._lock = RLock()
        self._idle = Condition(self._lock)
        self._reservations: set[UUID] = set()
        self._active_calls: set[UUID] = set()
        self._thread_reservations: dict[int, list[UUID]] = {}

    @property
    def tool_call_count(self) -> int:
        """Return the number of completed diagnostic calls."""

        with self._lock:
            return len(self._events)

    @property
    def reserved_call_count(self) -> int:
        """Return the number of admitted calls that have not yet recorded a result."""

        with self._lock:
            return len(self._reservations)

    @property
    def active_call_count(self) -> int:
        """Return admitted calls that have not left their execution boundary."""

        with self._lock:
            return len(self._active_calls)

    def reserve_call(self) -> "DiagnosticCallPermit":
        """Atomically reserve one budget slot before backend execution."""

        token = uuid4()
        thread_id = get_ident()
        with self._lock:
            admitted = len(self._events) + len(self._reservations)
            if self.max_tool_calls is not None and admitted >= self.max_tool_calls:
                raise ToolCallBudgetExceeded(
                    f"Diagnostic tool-call budget of {self.max_tool_calls} was exhausted"
                )
            self._reservations.add(token)
            self._active_calls.add(token)
            self._thread_reservations.setdefault(thread_id, []).append(token)
        return DiagnosticCallPermit(self, token, thread_id)

    def record(self, event: DiagnosticAuditEvent, result: DiagnosticResult) -> None:
        """Append audit metadata and retain evidence separately for provenance checks."""

        with self._lock:
            if event.session_id != self.session_id:
                raise ValueError("Audit event belongs to another diagnostic session")
            if result.tool_call_id != event.tool_call_id or result.tool != event.tool:
                raise ValueError("Audit event does not match its diagnostic result")
            if event.tool_call_id in self._results:
                raise ValueError("Duplicate diagnostic tool call ID")
            self._consume_current_reservation()
            self._events.append(event)
            self._results[event.tool_call_id] = result.model_copy(deep=True)

    def audit_records(self) -> tuple[DiagnosticAuditEvent, ...]:
        """Return an immutable view of session audit metadata."""

        with self._lock:
            return tuple(self._events)

    def assert_call_available(self) -> None:
        """Check availability without reserving a slot (prefer ``reserve_call``)."""

        with self._lock:
            admitted = len(self._events) + len(self._reservations)
            if self.max_tool_calls is not None and admitted >= self.max_tool_calls:
                raise ToolCallBudgetExceeded(
                    f"Diagnostic tool-call budget of {self.max_tool_calls} was exhausted"
                )

    def evidence(self, tool_call_id: UUID) -> DiagnosticResult | None:
        """Return a defensive copy of evidence for one completed tool call."""

        with self._lock:
            result = self._results.get(tool_call_id)
            return result.model_copy(deep=True) if result is not None else None

    def _consume_current_reservation(self) -> None:
        thread_id = get_ident()
        tokens = self._thread_reservations.get(thread_id)
        if not tokens:
            return
        token = tokens.pop()
        self._reservations.discard(token)
        if not tokens:
            del self._thread_reservations[thread_id]

    def _release_reservation(self, token: UUID, thread_id: int) -> None:
        with self._lock:
            self._reservations.discard(token)
            self._active_calls.discard(token)
            tokens = self._thread_reservations.get(thread_id)
            if tokens is not None:
                if token in tokens:
                    tokens.remove(token)
                if not tokens:
                    del self._thread_reservations[thread_id]
            self._idle.notify_all()

    def wait_for_idle(self, timeout_seconds: float) -> bool:
        """Wait a bounded time for all admitted backend executions to leave."""

        deadline = monotonic() + timeout_seconds
        with self._idle:
            while self._active_calls:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(remaining)
            return True


class DiagnosticCallPermit:
    """Context-managed ownership of one admitted diagnostic execution."""

    def __init__(self, session: DiagnosticSession, token: UUID, thread_id: int) -> None:
        self._session = session
        self._token = token
        self._thread_id = thread_id

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self._session._release_reservation(self._token, self._thread_id)


class ToolCallBudgetExceeded(RuntimeError):
    """Raised before a diagnostic backend call that would exceed the session budget."""
