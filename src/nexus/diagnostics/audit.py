"""In-memory diagnostic call accounting and safe audit metadata."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from nexus.diagnostics.models import DiagnosticSource


class DiagnosticAuditEvent(BaseModel):
    """One tool execution record without returned evidence or secrets."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    tool: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    normalized_arguments: dict[str, JsonValue]
    success: bool
    duration_ms: float = Field(ge=0)
    backend: DiagnosticSource
    result_count: int = Field(ge=0)


class DiagnosticSession:
    """Track tool usage now so later agent evaluation can impose budgets."""

    def __init__(self, session_id: UUID | None = None) -> None:
        self.session_id = session_id or uuid4()
        self._events: list[DiagnosticAuditEvent] = []

    @property
    def tool_call_count(self) -> int:
        """Return the number of completed diagnostic calls."""

        return len(self._events)

    def record(self, event: DiagnosticAuditEvent) -> None:
        """Append one immutable audit event."""

        if event.session_id != self.session_id:
            raise ValueError("Audit event belongs to another diagnostic session")
        self._events.append(event)

    def audit_records(self) -> tuple[DiagnosticAuditEvent, ...]:
        """Return an immutable view of session audit metadata."""

        return tuple(self._events)
