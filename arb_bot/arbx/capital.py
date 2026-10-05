"""Profit-compounding capital policy for the $3 live starter mode.

The policy is deliberately conservative: live deployment starts at $3, and only
realized positive P&L can increase the next trade's notional. A realized loss
never increases capital and is treated as a circuit-breaker event by the Hub.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CapitalAllocator:
    starter_usd: float = 3.0
    realized_profit_usd: float = 0.0

    @property
    def compounded_usd(self) -> float:
        return self.starter_usd + max(0.0, self.realized_profit_usd)

    def record(self, realized_pnl: float) -> None:
        if realized_pnl > 0:
            self.realized_profit_usd += realized_pnl

    def size(self, available_usd: float) -> float:
        return max(0.0, min(self.compounded_usd, available_usd))
