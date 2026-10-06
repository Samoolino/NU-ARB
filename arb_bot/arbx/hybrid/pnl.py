from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PnLModel:
    gross_usd: float
    fees_usd: float
    slippage_usd: float
    funding_usd: float = 0.0
    transfer_usd: float = 0.0
    network_usd: float = 0.0
    borrow_usd: float = 0.0
    fx_usd: float = 0.0
    latency_usd: float = 0.0
    partial_fill_reserve_usd: float = 0.0
    rebalancing_usd: float = 0.0
    safety_reserve_usd: float = 0.0

    @property
    def expected_net_usd(self) -> float:
        return self.gross_usd - (
            self.fees_usd + self.slippage_usd + self.funding_usd +
            self.transfer_usd + self.network_usd + self.borrow_usd +
            self.fx_usd + self.latency_usd
        )

    @property
    def worst_case_net_usd(self) -> float:
        return self.expected_net_usd - (
            self.partial_fill_reserve_usd + self.rebalancing_usd +
            self.safety_reserve_usd
        )


def gate_profit(model: PnLModel, min_profit_usd: float, min_worst_profit_usd: float) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if model.expected_net_usd < min_profit_usd:
        reasons.append("expected_net_profit_floor")
    if model.worst_case_net_usd < min_worst_profit_usd:
        reasons.append("worst_case_profit_floor")
    return not reasons, tuple(reasons)
