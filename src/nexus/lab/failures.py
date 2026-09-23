"""Concurrency-safe, ephemeral failure state for lab services."""

from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from time import sleep


class FailureEffect(StrEnum):
    """Bounded operational effects supported by the incident lab."""

    SERVICE_UNAVAILABLE = "service_unavailable"
    LATENCY = "latency"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"


@dataclass(frozen=True, slots=True)
class FailureDefinition:
    """A service-local fault definition without evaluator ground truth."""

    failure_id: str
    effect: FailureEffect
    delay_ms: int | None = None

    def __post_init__(self) -> None:
        if self.effect is FailureEffect.LATENCY:
            if self.delay_ms is None or not 50 <= self.delay_ms <= 10_000:
                raise ValueError("Latency failures require a delay from 50 to 10000 ms")
        elif self.delay_ms is not None:
            raise ValueError("Only latency failures may define delay_ms")


@dataclass(frozen=True, slots=True)
class FailureState:
    """Current ephemeral activation state."""

    active_failure: FailureDefinition | None


class UnknownFailureError(ValueError):
    """Raised when a controller does not support a requested failure."""


class FailureController:
    """Manage at most one active failure for a service process."""

    def __init__(self, definitions: tuple[FailureDefinition, ...]) -> None:
        if not definitions:
            raise ValueError("A failure controller requires at least one definition")
        self._definitions = {definition.failure_id: definition for definition in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("Failure IDs must be unique")
        self._active_failure_id: str | None = None
        self._lock = RLock()

    def list_definitions(self) -> tuple[FailureDefinition, ...]:
        """Return supported failures in stable ID order."""

        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def activate(self, failure_id: str) -> FailureState:
        """Activate a known failure, replacing any current failure."""

        with self._lock:
            if failure_id not in self._definitions:
                raise UnknownFailureError(f"Unknown failure: {failure_id}")
            self._active_failure_id = failure_id
            return self.state()

    def state(self) -> FailureState:
        """Return a consistent snapshot of current state."""

        with self._lock:
            active = (
                self._definitions[self._active_failure_id]
                if self._active_failure_id is not None
                else None
            )
            return FailureState(active_failure=active)

    def reset(self) -> FailureState:
        """Idempotently clear the active failure."""

        with self._lock:
            self._active_failure_id = None
            return self.state()

    def is_active(self, effect: FailureEffect) -> bool:
        """Return whether the active failure has the requested effect."""

        active = self.state().active_failure
        return active is not None and active.effect is effect

    def apply_latency(self) -> None:
        """Apply the active bounded deterministic latency, if any."""

        active = self.state().active_failure
        if active is not None and active.effect is FailureEffect.LATENCY:
            if active.delay_ms is None:
                raise RuntimeError("Validated latency failure has no delay")
            sleep(active.delay_ms / 1000)
