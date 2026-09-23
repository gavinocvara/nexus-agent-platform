import pytest

from nexus.lab.failures import (
    FailureController,
    FailureDefinition,
    FailureEffect,
    UnknownFailureError,
)


def test_activation_reset_and_idempotent_reset() -> None:
    controller = FailureController(
        (FailureDefinition("service_down", FailureEffect.SERVICE_UNAVAILABLE),)
    )

    assert controller.state().active_failure is None
    assert controller.activate("service_down").active_failure is not None
    assert controller.is_active(FailureEffect.SERVICE_UNAVAILABLE)
    assert controller.reset().active_failure is None
    assert controller.reset().active_failure is None


def test_unknown_failure_is_rejected() -> None:
    controller = FailureController(
        (FailureDefinition("service_down", FailureEffect.SERVICE_UNAVAILABLE),)
    )

    with pytest.raises(UnknownFailureError, match="Unknown failure"):
        controller.activate("not_supported")


def test_controller_state_is_isolated() -> None:
    definition = FailureDefinition("slow", FailureEffect.LATENCY, delay_ms=50)
    first = FailureController((definition,))
    second = FailureController((definition,))

    first.activate("slow")

    assert first.state().active_failure == definition
    assert second.state().active_failure is None


def test_failure_definitions_reject_unbounded_configuration() -> None:
    with pytest.raises(ValueError, match="50 to 10000"):
        FailureDefinition("too_slow", FailureEffect.LATENCY, delay_ms=20_000)
    with pytest.raises(ValueError, match="Only latency"):
        FailureDefinition("invalid", FailureEffect.SERVICE_UNAVAILABLE, delay_ms=100)
