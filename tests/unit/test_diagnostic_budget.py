from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from nexus.aegisops.context import InvestigatorContext
from nexus.aegisops.tools import execute_tool
from nexus.diagnostics.audit import DiagnosticSession, ToolCallBudgetExceeded
from nexus.diagnostics.service import DiagnosticServiceLayer


def _context(session: DiagnosticSession) -> InvestigatorContext:
    diagnostics = object.__new__(DiagnosticServiceLayer)
    diagnostics.session = session
    return InvestigatorContext(
        diagnostics=diagnostics,
        session=session,
        run_id=session.session_id,
    )


@pytest.mark.parametrize(("limit", "attempts"), [(1, 8), (12, 32)])
def test_parallel_attempts_never_overshoot_hard_budget(limit: int, attempts: int) -> None:
    session = DiagnosticSession(max_tool_calls=limit)
    context = _context(session)
    start = Barrier(attempts)

    def invoke() -> bool:
        start.wait()
        try:
            execute_tool(context, "list_services")
        except ToolCallBudgetExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=attempts) as pool:
        admitted = list(pool.map(lambda _index: invoke(), range(attempts)))

    assert sum(admitted) == limit
    assert session.tool_call_count == limit
    assert session.reserved_call_count == 0
    assert session.active_call_count == 0


def test_reservation_is_released_after_backend_exception() -> None:
    session = DiagnosticSession(max_tool_calls=1)
    with pytest.raises(RuntimeError):
        with session.reserve_call():
            raise RuntimeError("backend failed")
    assert session.tool_call_count == 0
    assert session.reserved_call_count == 0
    assert session.active_call_count == 0
    with session.reserve_call():
        pass
    assert session.reserved_call_count == 0
    assert session.active_call_count == 0
