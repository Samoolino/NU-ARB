"""Executors. Paper and Live implement the same interface so the engine never branches on mode."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from arbx.util import buy_with_quote


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


async def _ioc(ex, symbol: str, side: str, size: float, price: float) -> dict:
    o = await ex.create_order(symbol, "limit", side, size, price, {"timeInForce": "IOC"})
    if o.get("filled") is None or o.get("status") in (None, "open"):
        o = await ex.fetch_order(o["id"], symbol)
    return o


class LiveExecutor:
    """Sequential IOC limit orders. Each leg trades the ACTUAL amount received from the previous fill."""

    def __init__(self, ex, cfg):
        self.ex, self.cfg = ex, cfg

    async def execute_tri(self, o) -> TradeResult:
        amount, done = o.start, []
        for leg, lim in zip(o.legs, o.limits):
            m = self.ex.market(leg.symbol)
            raw = amount / lim if leg.side == "buy" else amount      # buying at <= lim can never overspend
            size = float(self.ex.amount_to_precision(leg.symbol, raw))
            try:
                if size <= 0:
                    raise NoFill("size rounds to zero")
                order = await _ioc(self.ex, leg.symbol, leg.side, size, lim)
                got = _received(order, leg.side, m)
                if got <= 0:
                    raise NoFill(f"{leg.symbol} {leg.side} unfilled")
            except NoFill:
                if not done:
                    return TradeResult(0.0, 0.0, ok=False)            # nothing executed -> benign
                await self._unwind(done)
                raise LegFailure(f"missed {leg.symbol} {leg.side} after earlier fills; unwind attempted")
            except Exception as e:
                if done:
                    try:
                        await self._unwind(done)
                    except Exception:
                        pass
                raise LegFailure(f"{leg.symbol} {leg.side}: {e!r} - CHECK ACCOUNT MANUALLY") from e
            done.append((leg, m, amount, got))
            amount = got
        return TradeResult(amount - o.start, amount)

    async def _free(self, ccy: str) -> float:
        return float(((await self.ex.fetch_balance()).get("free") or {}).get(ccy) or 0.0)

    async def _unwind(self, done) -> None:
        """Market-reverse executed legs, newest first. Accepts a bounded loss to escape inventory risk."""
        for leg, m, a_in, a_out in reversed(done):
            sym = leg.symbol
            if leg.side == "buy":                                     # we hold base -> sell it back
                held = min(a_out, await self._free(m["base"])) * 0.999
                await self.ex.create_order(sym, "market", "sell", float(self.ex.amount_to_precision(sym, held)))
            else:                                                     # we hold quote -> buy base back
                held = min(a_out, await self._free(m["quote"])) * 0.995
                ob = await self.ex.fetch_order_book(sym, 10)
                r = buy_with_quote(ob["asks"], held)
                if r is None:
                    raise LegFailure(f"unwind depth insufficient on {sym}")
                await self.ex.create_order(sym, "market", "buy", float(self.ex.amount_to_precision(sym, r[0])))


class CrossExecutor:
    """Simultaneous IOC limit orders using pre-funded inventory; unmatched fills halt for manual review."""

    def __init__(self, exchanges: dict):
        self.exs = exchanges

    async def execute(self, o) -> TradeResult:
        eb, es = self.exs[o.buy_ex], self.exs[o.sell_ex]
        size = min(float(eb.amount_to_precision(o.symbol, o.base)), float(es.amount_to_precision(o.symbol, o.base)))
        if size <= 0:
            return TradeResult(0.0, ok=False)
        rb, rs = await asyncio.gather(_ioc(eb, o.symbol, "buy", size, o.limit_buy),
                                      _ioc(es, o.symbol, "sell", size, o.limit_sell), return_exceptions=True)
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
