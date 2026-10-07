"""Executors. Paper and Live implement the same interface so the engine never branches on mode."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from arbx.util import buy_with_quote
from arbx.execution_gate import (
    ExecutionAuthorizationError,
    ExecutionGate,
    ExecutionOrder,
    ExecutionPermit,
    opportunity_fingerprint,
)


@dataclass(slots=True)
class TradeResult:
    pnl: float
    final: float = 0.0
    ok: bool = True


class LegFailure(Exception):
    """Ambiguous/stranded state (order error, imbalance). Bot must halt; a human reviews the exchange account."""


class NoFill(Exception):
    """IOC order expired with zero fill. Harmless if it happens on the FIRST leg."""


class PaperExecutor:
    def __init__(self, cfg):
        self.pen = cfg.paper_penalty_bps / 1e4

    async def execute_tri(self, o) -> TradeResult:
        # modelled slippage, but never below the limit-price floor (IOC limits make that floor real)
        final = max(o.expected_final * (1.0 - self.pen), o.worst_final)
        return TradeResult(final - o.start, final)

    async def execute_cross(self, o) -> TradeResult:
        return TradeResult(max(o.expected_usd - o.cost * self.pen, o.worst_usd))


def _received(order: dict, side: str, market: dict) -> float:
    filled = float(order.get("filled") or 0.0)
    if filled <= 0:
        return 0.0
    if side == "buy":
        got, ccy = filled, market["base"]
    else:
        got = float(order.get("cost") or filled * float(order.get("average") or order.get("price") or 0.0))
        ccy = market["quote"]
    fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
    for f in fees:
        if f and f.get("currency") == ccy and f.get("cost"):
            got -= float(f["cost"])
    return got


async def _ioc(ex, symbol: str, side: str, size: float, price: float,
               gate: ExecutionGate, permit: ExecutionPermit, *,
               strategy: str, route_id: str, opportunity_id: str,
               venue: str, order_index: int) -> dict:
    order_scope = ExecutionOrder(order_index, venue, symbol, side, "limit", size, price)
    gate.authorize_order(
        permit, order_scope, strategy=strategy, route_id=route_id,
        opportunity_id=opportunity_id,
    )
    gate.note_order_attempt(permit, order_scope)
    o = await ex.create_order(symbol, "limit", side, size, price, {"timeInForce": "IOC"})
    if o.get("filled") is None or o.get("status") in (None, "open"):
        o = await ex.fetch_order(o["id"], symbol)
    return o


class LiveExecutor:
    """Sequential IOC limit orders. Each leg trades the ACTUAL amount received from the previous fill."""

    def __init__(self, ex, cfg, execution_gate: ExecutionGate | None = None):
        self.ex, self.cfg = ex, cfg
        self.execution_gate = execution_gate or ExecutionGate(cfg)

    async def execute_tri(self, o, permit) -> TradeResult:
        route_id = f"triangular:{permit.venues[0]}" if permit.venues else ""
        opportunity_id = opportunity_fingerprint("triangular", o)
        if permit.strategy != "triangular":
            raise ExecutionAuthorizationError("execution_permit_scope_mismatch")
        amount, done = o.start, []
        for index, (leg, lim) in enumerate(zip(o.legs, o.limits)):
            m = self.ex.market(leg.symbol)
            raw = amount / lim if leg.side == "buy" else amount      # buying at <= lim can never overspend
            size = float(self.ex.amount_to_precision(leg.symbol, raw))
            try:
                if size <= 0:
                    raise NoFill("size rounds to zero")
                order = await _ioc(
                    self.ex, leg.symbol, leg.side, size, lim,
                    self.execution_gate, permit, strategy="triangular",
                    route_id=route_id, opportunity_id=opportunity_id,
                    venue=permit.venues[0], order_index=index,
                )
                got = _received(order, leg.side, m)
                if got <= 0:
                    raise NoFill(f"{leg.symbol} {leg.side} unfilled")
            except NoFill:
                if not done:
                    return TradeResult(0.0, 0.0, ok=False)            # nothing executed -> benign
                await self._unwind(done, permit)
                raise LegFailure(f"missed {leg.symbol} {leg.side} after earlier fills; unwind attempted")
            except Exception as e:
                if done:
                    try:
                        await self._unwind(done, permit)
                    except Exception:
                        pass
                raise LegFailure(f"{leg.symbol} {leg.side}: {e!r} - CHECK ACCOUNT MANUALLY") from e
            done.append((leg, m, amount, got))
            amount = got
        return TradeResult(amount - o.start, amount)

    async def _free(self, ccy: str) -> float:
        return float(((await self.ex.fetch_balance()).get("free") or {}).get(ccy) or 0.0)

    async def _unwind(self, done, permit) -> None:
        """Market-reverse executed legs, newest first. Accepts a bounded loss to escape inventory risk."""
        original_indexes = {
            (scope.symbol, scope.side): scope.index for scope in permit.orders
        }
        for leg, m, a_in, a_out in reversed(done):
            sym = leg.symbol
            if leg.side == "buy":                                     # we hold base -> sell it back
                held = min(a_out, await self._free(m["base"])) * 0.999
                order = ExecutionOrder(
                    original_indexes[(sym, leg.side)], permit.venues[0], sym,
                    "sell", "market", float(self.ex.amount_to_precision(sym, held)), None,
                )
                emergency = self.execution_gate.authorize_recovery(permit, order)
                self.execution_gate.authorize_recovery_order(permit, emergency)
                await self.ex.create_order(sym, "market", "sell", order.quantity)
            else:                                                     # we hold quote -> buy base back
                held = min(a_out, await self._free(m["quote"])) * 0.995
                ob = await self.ex.fetch_order_book(sym, 10)
                r = buy_with_quote(ob["asks"], held)
                if r is None:
                    raise LegFailure(f"unwind depth insufficient on {sym}")
                order = ExecutionOrder(
                    original_indexes[(sym, leg.side)], permit.venues[0], sym,
                    "buy", "market", float(self.ex.amount_to_precision(sym, r[0])), None,
                )
                emergency = self.execution_gate.authorize_recovery(permit, order)
                self.execution_gate.authorize_recovery_order(permit, emergency)
                await self.ex.create_order(sym, "market", "buy", order.quantity)


class CrossExecutor:
    """Simultaneous IOC limit orders using pre-funded inventory; unmatched fills halt for manual review."""

    def __init__(self, exchanges: dict, cfg, execution_gate: ExecutionGate | None = None):
        self.exs = exchanges
        self.execution_gate = execution_gate or ExecutionGate(cfg)

    async def execute(self, o, permit) -> TradeResult:
        route_id = f"cross:{o.buy_ex}>{o.sell_ex}"
        opportunity_id = opportunity_fingerprint("cross", o)
        if permit.strategy != "cross":
            raise ExecutionAuthorizationError("execution_permit_scope_mismatch")
        eb, es = self.exs[o.buy_ex], self.exs[o.sell_ex]
        size = min(float(eb.amount_to_precision(o.symbol, o.base)), float(es.amount_to_precision(o.symbol, o.base)))
        if size <= 0:
            return TradeResult(0.0, ok=False)
        rb, rs = await asyncio.gather(
            _ioc(eb, o.symbol, "buy", size, o.limit_buy, self.execution_gate, permit,
                 strategy="cross", route_id=route_id, opportunity_id=opportunity_id,
                 venue=o.buy_ex, order_index=0),
            _ioc(es, o.symbol, "sell", size, o.limit_sell, self.execution_gate, permit,
                 strategy="cross", route_id=route_id, opportunity_id=opportunity_id,
                 venue=o.sell_ex, order_index=1),
            return_exceptions=True,
        )
        for r in (rb, rs):
            if isinstance(r, Exception):
                raise LegFailure(f"cross order error: {r!r} - CHECK BOTH ACCOUNTS")
        fb, fs = float(rb.get("filled") or 0.0), float(rs.get("filled") or 0.0)
        if fb <= 0 and fs <= 0:
            return TradeResult(0.0, ok=False)
        if fb != fs:
            raise LegFailure(f"inventory imbalance: bought {fb} vs sold {fs} {o.symbol} - REBALANCE MANUALLY")
        cost = float(rb.get("cost") or fb * o.limit_buy)
        proceeds = float(rs.get("cost") or fs * o.limit_sell)
        fee_q = sum(float(f.get("cost") or 0) for r in (rb, rs) for f in (r.get("fees") or [r.get("fee") or {}])
                    if f and f.get("currency") == o.quote_ccy)
        return TradeResult(proceeds - cost - fee_q)
