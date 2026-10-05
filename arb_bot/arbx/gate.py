"""Modeled-profit and execution-safety gates.

Live policy: start at $3, compound only realized profits into the next trade,
and stop immediately after any realized loss. There is no fixed session-loss
ceiling; the engine is fail-closed on realized loss instead.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(slots=True)
class Decision:
    ok: bool
    reason: str = ""


OK = Decision(True)


class RiskManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pnl = 0.0
        self.failures = 0
        self.halted = False
        self.reason = ""
        self._stamps: deque = deque()

    def halt(self, reason: str) -> None:
        if not self.halted:
            self.halted, self.reason = True, reason

    def rate_ok(self) -> bool:
        now = time.monotonic()
        while self._stamps and now - self._stamps[0] > 60.0:
            self._stamps.popleft()
        return len(self._stamps) < self.cfg.max_trades_per_min

    @property
    def compounded_capital(self) -> float:
        return self.cfg.starter_capital_usd + max(0.0, self.pnl)

    def record(self, pnl: float, ok: bool) -> None:
        self._stamps.append(time.monotonic())
        self.pnl += pnl
        self.failures = 0 if ok else self.failures + 1
        if self.cfg.mode == "live" and self.cfg.compound_profits:
            self.cfg.trade_size_usd = self.compounded_capital
        if self.cfg.mode == "live" and self.cfg.halt_on_realized_loss and pnl < 0:
            self.halt(f"realized loss protection triggered ({pnl:.6f} USD)")
        elif self.failures >= self.cfg.max_consecutive_failures:
            self.halt("too many consecutive failed/unfilled cycles")


class ProfitGate:
    def __init__(self, cfg, risk: RiskManager):
        self.cfg, self.risk = cfg, risk

    def _common(self, age_ms, net_bps, worst_bps, worst_usd, lat_ok) -> Decision:
        c = self.cfg
        if self.risk.halted:
            return Decision(False, "halted")
        if not lat_ok:
            return Decision(False, "latency_degraded")
        if not self.risk.rate_ok():
            return Decision(False, "rate_limit")
        if age_ms > c.max_book_age_ms:
            return Decision(False, "stale_book")
        if net_bps < c.min_net_bps:
            return Decision(False, "net_edge")
        if worst_bps < c.min_worst_bps:
            return Decision(False, "worst_case_below_floor")
        if worst_usd < c.min_profit_usd:
            return Decision(False, "profit_below_min_usd")
        if c.mode == "live" and worst_usd <= 0:
            return Decision(False, "non_positive_worst_case")
        return OK

    @staticmethod
    def _min_ok(mlimits, symbol, amount, cost) -> bool:
        mn_amt, mn_cost = mlimits.get(symbol, (None, None))
        return not ((mn_amt and amount < mn_amt) or (mn_cost and cost < mn_cost))

    def check_tri(self, o, mlimits, lat_ok: bool, free: float) -> Decision:
        d = self._common(o.age_ms, o.net_bps, o.worst_bps, o.worst_final - o.start, lat_ok)
        if not d.ok:
            return d
        for leg, (a_in, a_out) in zip(o.legs, o.path):
            amount, cost = (a_out, a_in) if leg.side == "buy" else (a_in, a_out)
            if not self._min_ok(mlimits, leg.symbol, amount, cost):
                return Decision(False, "below_exchange_minimum")
        if free < o.start:
            return Decision(False, "insufficient_balance")
        return OK

    def check_cross(self, o, mlim_buy, mlim_sell, lat_ok: bool, free_quote: float, free_base: float) -> Decision:
        d = self._common(o.age_ms, o.net_bps, o.worst_bps, o.worst_usd, lat_ok)
        if not d.ok:
            return d
        if not (self._min_ok(mlim_buy, o.symbol, o.base, o.cost) and self._min_ok(mlim_sell, o.symbol, o.base, o.cost)):
            return Decision(False, "below_exchange_minimum")
        if free_quote < o.cost * 1.002 or free_base < o.base:
            return Decision(False, "insufficient_inventory")
        return OK
