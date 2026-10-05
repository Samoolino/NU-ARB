"""Capital/PnL boundary primitives.

Capital movements are deliberately separate from realized trading PnL.
This module is pure policy logic; exchange/account integration remains read-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CapitalEventType(StrEnum):
    EXTERNAL_INJECTION = "EXTERNAL_INJECTION"
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"
    EXTERNAL_WITHDRAWAL = "EXTERNAL_WITHDRAWAL"
    FUNDING_OR_YIELD = "FUNDING_OR_YIELD"
    ADJUSTMENT_OR_DUST = "ADJUSTMENT_OR_DUST"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CapitalDelta:
    exchange_id: str
    asset: str
    before: float
    after: float
    observed_at: float

    @property
    def amount(self) -> float:
        return self.after - self.before


def classify_unreconciled_delta(delta: CapitalDelta) -> CapitalEventType:
    """Unreconciled balance movement is never trading PnL."""
    if abs(delta.amount) <= 1e-12:
        return CapitalEventType.ADJUSTMENT_OR_DUST
    return CapitalEventType.UNKNOWN


def target_profit_progress(realized_trade_pnl: float, target_profit: float | None) -> float:
    """Compute progress from verified trading PnL only; capital is intentionally excluded."""
    if target_profit is None or target_profit <= 0:
        return 0.0
    return min(100.0, max(0.0, realized_trade_pnl / target_profit * 100.0))
