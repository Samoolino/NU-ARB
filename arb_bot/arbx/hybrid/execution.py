from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Protocol


def _finite_number(value: Any) -> bool:
    """Return False for booleans, non-numbers, infinities, NaN, and overflow."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


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


@dataclass(frozen=True, slots=True)
class FillSnapshot:
    requested: float
    filled: float
    remaining: float
    order_id: str | None = None
    status: str | None = None


class ExecutionAdapter(Protocol):
    async def create_order(self, request: Any) -> dict[str, Any]: ...
    async def get_order(self, order_id: str, symbol: str) -> dict[str, Any]: ...
    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]: ...


@dataclass
class ExecutionCoordinator:
    """Fail-closed execution/reconciliation state machine.

    This is deliberately transport-neutral: callers provide a VenueAdapter-compatible
    execution object. It never assumes that a submitted order is filled.
    """

    state: ExecutionState = ExecutionState.PLANNED
    events: list[dict[str, Any]] | None = None
    realized_loss_usd: float = 0.0
    halted: bool = False

    def __post_init__(self) -> None:
        if self.events is None:
            self.events = []

    def transition(self, state: ExecutionState, **detail: Any) -> None:
        if self.halted and state not in {ExecutionState.HALTED, ExecutionState.RECONCILING}:
            raise RuntimeError("execution coordinator is halted")
        self.state = state
        self.events.append({"state": state.value, **detail})

    def gate(self, *, expected_net_usd: float, worst_case_net_usd: float,
             min_profit_usd: float = 0.0,
             min_worst_profit_usd: float = 0.0) -> bool:
        if self.halted:
            return False
        if not all(_finite_number(value) for value in (
            expected_net_usd, worst_case_net_usd, min_profit_usd, min_worst_profit_usd
        )) or min_profit_usd < 0 or min_worst_profit_usd < 0:
            self.halted = True
            self.transition(
                ExecutionState.HALTED,
                reason="invalid_profitability_evidence",
            )
            return False
        if expected_net_usd < min_profit_usd or worst_case_net_usd < min_worst_profit_usd:
            self.halted = True
            self.transition(
                ExecutionState.HALTED,
                reason="profitability_gate",
                expected_net_usd=expected_net_usd,
                worst_case_net_usd=worst_case_net_usd,
            )
            return False
        return True

    def on_submission(self, order_id: str | None = None) -> None:
        self.transition(ExecutionState.SUBMITTING, order_id=order_id)

    def on_fill(self, requested: float, filled: float, order_id: str | None = None) -> ExecutionState:
        if not _finite_number(requested) or not _finite_number(filled):
            raise ValueError("fill quantities must be finite numbers")
        if requested <= 0:
            raise ValueError("requested quantity must be positive")
        if filled < 0 or filled > requested:
            raise ValueError("filled quantity must be within requested quantity")
        if filled == 0:
            self.transition(ExecutionState.FAILED, reason="no_fill", order_id=order_id)
        elif filled < requested:
            self.transition(
                ExecutionState.PARTIALLY_FILLED,
                requested=requested,
                filled=filled,
                remaining=requested - filled,
                order_id=order_id,
            )
        else:
            self.transition(
                ExecutionState.FILLED,
                requested=requested,
                filled=filled,
                remaining=0.0,
                order_id=order_id,
            )
        return self.state

    def on_executor_success(self) -> None:
        """Keep a successful executor response in reconciliation, not a fabricated fill state.

        The executor result has PnL/final value but not a normalized per-order fill
        ledger. Call on_fill() only when actual requested/filled quantities exist.
        """
        self.transition(ExecutionState.RECONCILING, executor_result="ok")

    def require_reconciliation(self) -> None:
        self.transition(ExecutionState.RECONCILING)

    def on_realized_pnl(self, pnl_usd: float) -> None:
        if not _finite_number(pnl_usd):
            self.halted = True
            self.transition(
                ExecutionState.HALTED,
                reason="invalid_realized_pnl",
            )
            return
        if pnl_usd < 0:
            self.realized_loss_usd += abs(pnl_usd)
            self.halted = True
            self.transition(
                ExecutionState.HALTED,
                reason="realized_loss",
                pnl_usd=pnl_usd,
                cumulative_loss_usd=self.realized_loss_usd,
            )

    def complete(self) -> None:
        if self.halted:
            self.transition(ExecutionState.HALTED, reason="halted")
            return
        self.transition(ExecutionState.COMPLETED)

    def fail(self, reason: str) -> None:
        self.transition(ExecutionState.FAILED, reason=reason)
