"""Minimal local context passed to investigator tools."""

from dataclasses import dataclass
from uuid import UUID

from nexus.diagnostics.audit import DiagnosticSession
from nexus.diagnostics.service import DiagnosticServiceLayer


@dataclass(frozen=True, slots=True)
class InvestigatorContext:
    diagnostics: DiagnosticServiceLayer
    session: DiagnosticSession
    run_id: UUID
