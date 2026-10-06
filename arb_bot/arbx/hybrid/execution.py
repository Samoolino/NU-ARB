from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ExecutionState(str, Enum):
    PLANNED = "PLANNED"
    SUBMITTING = "SUBMITTING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    HEDGING = "HEDGING"
    FILLED = "FILLED"
    CANCELLING = "CANCELLING"
    RECONCILING = "RECONCILING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    HALTED = "HALTED"


@dataclass
class ExecutionCoordinator:
    """Stateful execution boundary.

    The coordinator deliberately accepts venue adapters rather than CCXT objects.
    Actual live order submission remains behind the existing worker controls until
    reconciliation and partial-fill policies are wired end-to-end.
    """

    state: ExecutionState = ExecutionState.PLANNED
    events: list[dict[str, Any]] | None = None

    def __post_init__(self):
        if self.events is None:
            self.events = []

    def transition(self, state: ExecutionState, **detail: Any) -> None:
        self.state = state
        self.events.append({"state": state.value, **detail})

    def on_fill(self, requested: float, filled: float) -> ExecutionState:
        if filled <= 0:
            self.transition(ExecutionState.FAILED, reason="no_fill")
        elif filled < requested:
            self.transition(ExecutionState.PARTIALLY_FILLED, requested=requested, filled=filled)
        else:
            self.transition(ExecutionState.FILLED, requested=requested, filled=filled)
        return self.state
