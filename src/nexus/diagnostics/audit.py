"""In-memory diagnostic call accounting and safe audit metadata."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from nexus.diagnostics.models import DiagnosticResult, DiagnosticSource


class DiagnosticAuditEvent(BaseModel):
    """One tool execution record without returned evidence or secrets."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    tool_call_id: UUID
    tool: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    normalized_arguments: dict[str, JsonValue]
    success: bool
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

    @property
    def tool_call_count(self) -> int:
        """Return the number of completed diagnostic calls."""

        return len(self._events)

    def record(self, event: DiagnosticAuditEvent, result: DiagnosticResult) -> None:
        """Append audit metadata and retain evidence separately for provenance checks."""

        if event.session_id != self.session_id:
            raise ValueError("Audit event belongs to another diagnostic session")
        if result.tool_call_id != event.tool_call_id or result.tool != event.tool:
            raise ValueError("Audit event does not match its diagnostic result")
        if event.tool_call_id in self._results:
            raise ValueError("Duplicate diagnostic tool call ID")
        self._events.append(event)
        self._results[event.tool_call_id] = result.model_copy(deep=True)

    def audit_records(self) -> tuple[DiagnosticAuditEvent, ...]:
        """Return an immutable view of session audit metadata."""

        return tuple(self._events)

    def assert_call_available(self) -> None:
        """Fail before backend access when this session has exhausted its hard budget."""

        if self.max_tool_calls is not None and self.tool_call_count >= self.max_tool_calls:
            raise ToolCallBudgetExceeded(
                f"Diagnostic tool-call budget of {self.max_tool_calls} was exhausted"
            )

    def evidence(self, tool_call_id: UUID) -> DiagnosticResult | None:
        """Return a defensive copy of evidence for one completed tool call."""

        result = self._results.get(tool_call_id)
        return result.model_copy(deep=True) if result is not None else None


class ToolCallBudgetExceeded(RuntimeError):
    """Raised before a diagnostic backend call that would exceed the session budget."""
