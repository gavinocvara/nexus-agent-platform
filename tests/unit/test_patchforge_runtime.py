"""PatchForge Runtime lifecycle tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from nexus.atlas.models import SourceRevision
from nexus.patchforge.models import (
    PatchForgeFailure,
    PatchForgePhase,
    PatchOutcome,
    PhaseBudget,
    RunBudgets,
    RunIdentity,
)
from nexus.patchforge.runtime import (
    LIFECYCLE_TRANSITIONS,
    PatchForgeLifecycle,
    RuntimeSnapshot,
    RuntimeTransition,
    RuntimeTransitionError,
    RuntimeTransitionKind,
    require_lifecycle_transition,
)

NOW = datetime(2026, 9, 28, 8, tzinfo=UTC)

EXPECTED_TRANSITIONS = {
    (PatchForgePhase.CREATED, PatchForgePhase.PROVISIONING),
    (PatchForgePhase.PROVISIONING, PatchForgePhase.RECON),
    (PatchForgePhase.RECON, PatchForgePhase.HYPOTHESIS),
    (PatchForgePhase.HYPOTHESIS, PatchForgePhase.REPRODUCE),
    (PatchForgePhase.REPRODUCE, PatchForgePhase.IMPLEMENT),
    (PatchForgePhase.IMPLEMENT, PatchForgePhase.TARGETED_VALIDATE),
    (PatchForgePhase.TARGETED_VALIDATE, PatchForgePhase.IMPLEMENT),
    (PatchForgePhase.TARGETED_VALIDATE, PatchForgePhase.FULL_VALIDATE),
    (PatchForgePhase.FULL_VALIDATE, PatchForgePhase.SELF_REVIEW),
    (PatchForgePhase.SELF_REVIEW, PatchForgePhase.FINALIZE),
    (PatchForgePhase.FINALIZE, PatchForgePhase.REPORTED),
    (PatchForgePhase.REPORTED, PatchForgePhase.CLEANUP),
    (PatchForgePhase.CLEANUP, PatchForgePhase.CLOSED),
}


def _phase_budget() -> PhaseBudget:
    return PhaseBudget(
        max_tool_calls=2,
        max_duration_seconds=30,
        max_output_bytes=10_000,
    )


def _budgets(*, loops: int = 2) -> RunBudgets:
    budget = _phase_budget()
    return RunBudgets(
        provisioning=budget,
        recon=budget,
        hypothesis=budget,
        reproduce=budget,
        implement=budget,
        targeted_validate=budget,
        full_validate=budget,
        self_review=budget,
        finalization_reserve=budget,
        cleanup=budget,
        max_implementation_loops=loops,
        max_total_tool_calls=20,
        max_total_duration_seconds=300,
    )


def _identity() -> RunIdentity:
    source = SourceRevision(
        repository_url="https://example.invalid/repository",
        commit_sha="a" * 40,
    )
    return RunIdentity(
        run_id=UUID(int=1),
        task_id=UUID(int=2),
        atlas_job_id=UUID(int=3),
        atlas_execution_id=UUID(int=4),
        agent_id="patchforge.engineer",
        source=source,
        repository_profile_id="fixture.python",
        repository_profile_sha256="b" * 64,
        task_sha256="c" * 64,
        engine_kind="scripted",
        engine_version="v1",
        created_at=NOW,
    )


def _lifecycle(*, loops: int = 2) -> PatchForgeLifecycle:
    return PatchForgeLifecycle(
        identity=_identity(),
        budgets=_budgets(loops=loops),
        clock=lambda: NOW,
    )


def _advance_to(lifecycle: PatchForgeLifecycle, target: PatchForgePhase) -> None:
    path = [
        PatchForgePhase.PROVISIONING,
        PatchForgePhase.RECON,
        PatchForgePhase.HYPOTHESIS,
        PatchForgePhase.REPRODUCE,
        PatchForgePhase.IMPLEMENT,
        PatchForgePhase.TARGETED_VALIDATE,
        PatchForgePhase.FULL_VALIDATE,
        PatchForgePhase.SELF_REVIEW,
        PatchForgePhase.FINALIZE,
    ]
    for phase in path:
        lifecycle.advance(phase)
        if phase is target:
            return
    raise AssertionError(f"Unsupported helper target: {target}")


def test_transition_table_is_complete_and_exact() -> None:
    actual = {
        (source, target) for source, targets in LIFECYCLE_TRANSITIONS.items() for target in targets
    }
    assert set(LIFECYCLE_TRANSITIONS) == set(PatchForgePhase)
    assert actual == EXPECTED_TRANSITIONS


@pytest.mark.parametrize("source", list(PatchForgePhase))
@pytest.mark.parametrize("target", list(PatchForgePhase))
def test_every_valid_and_invalid_transition(
    source: PatchForgePhase,
    target: PatchForgePhase,
) -> None:
    if (source, target) in EXPECTED_TRANSITIONS:
        require_lifecycle_transition(source, target)
    else:
        with pytest.raises(RuntimeTransitionError, match="Invalid PatchForge runtime transition"):
            require_lifecycle_transition(source, target)


def test_happy_path_records_every_transition_and_closes() -> None:
    lifecycle = _lifecycle()
    _advance_to(lifecycle, PatchForgePhase.FINALIZE)
    lifecycle.report()
    lifecycle.begin_cleanup()
    lifecycle.close()

    snapshot = lifecycle.snapshot
    assert snapshot.phase is PatchForgePhase.CLOSED
    assert snapshot.outcome is None
    assert snapshot.failure is None
    assert [item.sequence for item in snapshot.transitions] == list(range(1, 13))
    assert snapshot.transitions[-3].kind is RuntimeTransitionKind.REPORT
    assert snapshot.transitions[-2].kind is RuntimeTransitionKind.CLEANUP
    assert snapshot.transitions[-1].kind is RuntimeTransitionKind.CLOSE


def test_implementation_retry_is_bounded_and_attested() -> None:
    lifecycle = _lifecycle(loops=1)
    _advance_to(lifecycle, PatchForgePhase.TARGETED_VALIDATE)
    retry = lifecycle.advance(PatchForgePhase.IMPLEMENT)
    lifecycle.advance(PatchForgePhase.TARGETED_VALIDATE)

    assert retry.kind is RuntimeTransitionKind.RETRY
    assert retry.implementation_loops == 1
    assert lifecycle.snapshot.implementation_loops == 1
    with pytest.raises(RuntimeTransitionError, match="loop budget is exhausted"):
        lifecycle.advance(PatchForgePhase.IMPLEMENT)


@pytest.mark.parametrize(
    ("failure", "outcome"),
    [
        (PatchForgeFailure.BUDGET_EXHAUSTED, PatchOutcome.PARTIAL),
        (PatchForgeFailure.POLICY_DENIED, PatchOutcome.POLICY_VIOLATION),
        (PatchForgeFailure.CANCELLED, PatchOutcome.CANCELLED),
        (PatchForgeFailure.SANDBOX_ERROR, PatchOutcome.SANDBOX_FAILED),
        (PatchForgeFailure.WORKSPACE_ERROR, PatchOutcome.ABORTED),
        (PatchForgeFailure.ENGINE_ERROR, PatchOutcome.ABORTED),
        (PatchForgeFailure.VALIDATION_FAILED, PatchOutcome.PARTIAL),
        (PatchForgeFailure.ATTESTATION_FAILED, PatchOutcome.ABORTED),
    ],
)
def test_failure_outcome_is_deterministic(
    failure: PatchForgeFailure,
    outcome: PatchOutcome,
) -> None:
    lifecycle = _lifecycle()
    _advance_to(lifecycle, PatchForgePhase.REPRODUCE)

    transition = lifecycle.fail(failure)

    assert transition.target is PatchForgePhase.FINALIZE
    assert transition.failure is failure
    assert lifecycle.snapshot.outcome is outcome
    assert lifecycle.snapshot.failure is failure
    lifecycle.report()
    lifecycle.begin_cleanup()
    lifecycle.close()
    assert lifecycle.snapshot.phase is PatchForgePhase.CLOSED


def test_failure_during_finalization_routes_directly_to_cleanup() -> None:
    lifecycle = _lifecycle()
    _advance_to(lifecycle, PatchForgePhase.FINALIZE)

    transition = lifecycle.fail(PatchForgeFailure.ATTESTATION_FAILED)

    assert transition.target is PatchForgePhase.CLEANUP
    lifecycle.close()
    assert lifecycle.snapshot.phase is PatchForgePhase.CLOSED


def test_finalization_failure_preserves_an_existing_primary_failure() -> None:
    lifecycle = _lifecycle()
    _advance_to(lifecycle, PatchForgePhase.REPRODUCE)
    lifecycle.fail(PatchForgeFailure.BUDGET_EXHAUSTED)

    transition = lifecycle.finalization_failed(PatchForgeFailure.ENGINE_ERROR)

    assert transition.target is PatchForgePhase.CLEANUP
    assert transition.failure is PatchForgeFailure.ENGINE_ERROR
    assert lifecycle.snapshot.failure is PatchForgeFailure.BUDGET_EXHAUSTED
    assert lifecycle.snapshot.outcome is PatchOutcome.PARTIAL


def test_cleanup_failure_closes_and_overrides_success_outcome() -> None:
    lifecycle = _lifecycle()
    _advance_to(lifecycle, PatchForgePhase.FINALIZE)
    lifecycle.report()
    lifecycle.begin_cleanup()

    transition = lifecycle.cleanup_failed()

    assert transition.target is PatchForgePhase.CLOSED
    assert lifecycle.snapshot.cleanup_failed is True
    assert lifecycle.snapshot.failure is PatchForgeFailure.CLEANUP_FAILED
    assert lifecycle.snapshot.outcome is PatchOutcome.ABORTED


def test_failure_cannot_be_reclassified_or_resume_success() -> None:
    lifecycle = _lifecycle()
    _advance_to(lifecycle, PatchForgePhase.HYPOTHESIS)
    lifecycle.fail(PatchForgeFailure.BUDGET_EXHAUSTED)

    with pytest.raises(RuntimeTransitionError, match="already classified"):
        lifecycle.fail(PatchForgeFailure.ENGINE_ERROR)
    with pytest.raises(RuntimeTransitionError, match="failed runtime cannot resume"):
        lifecycle.advance(PatchForgePhase.REPORTED)


def test_cleanup_failure_classification_is_cleanup_only() -> None:
    lifecycle = _lifecycle()

    with pytest.raises(RuntimeTransitionError, match="requires the cleanup phase"):
        lifecycle.fail(PatchForgeFailure.CLEANUP_FAILED)


def test_cleanup_failure_preserves_primary_failure() -> None:
    lifecycle = _lifecycle()
    _advance_to(lifecycle, PatchForgePhase.REPRODUCE)
    lifecycle.fail(PatchForgeFailure.BUDGET_EXHAUSTED)
    lifecycle.report()
    lifecycle.begin_cleanup()

    lifecycle.cleanup_failed()

    snapshot = lifecycle.snapshot
    assert snapshot.cleanup_failed is True
    assert snapshot.failure is PatchForgeFailure.BUDGET_EXHAUSTED
    assert snapshot.outcome is PatchOutcome.PARTIAL


def test_report_cleanup_and_close_require_explicit_phases() -> None:
    lifecycle = _lifecycle()

    with pytest.raises(RuntimeTransitionError, match="only from finalization"):
        lifecycle.report()
    with pytest.raises(RuntimeTransitionError, match="only after a report"):
        lifecycle.begin_cleanup()
    with pytest.raises(RuntimeTransitionError, match="only after cleanup"):
        lifecycle.close()
    with pytest.raises(RuntimeTransitionError, match="only during cleanup"):
        lifecycle.cleanup_failed()


def test_snapshot_rejects_noncontiguous_or_cross_run_transcript() -> None:
    transition = RuntimeTransition(
        run_id=UUID(int=99),
        sequence=2,
        source=PatchForgePhase.CREATED,
        target=PatchForgePhase.PROVISIONING,
        kind=RuntimeTransitionKind.PROGRESS,
        implementation_loops=0,
        occurred_at=NOW,
    )

    with pytest.raises(ValidationError, match="sequence must be contiguous"):
        RuntimeSnapshot(
            run_id=UUID(int=1),
            phase=PatchForgePhase.PROVISIONING,
            implementation_loops=0,
            max_implementation_loops=1,
            transitions=[transition],
        )


def test_strict_transition_models_reject_coercion() -> None:
    with pytest.raises(ValidationError):
        RuntimeTransition.model_validate(
            {
                "run_id": str(UUID(int=1)),
                "sequence": "1",
                "source": "created",
                "target": "provisioning",
                "kind": "progress",
                "implementation_loops": 0,
                "occurred_at": NOW.isoformat(),
            }
        )
