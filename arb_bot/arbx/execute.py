"""Executors. Paper and Live implement the same interface so the engine never branches on mode."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from arbx.order_reconciliation import OrderLedger

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


async def _find_client_order(ex,symbol,client_order_id):
    for name in ("fetch_open_orders","fetch_orders","fetch_closed_orders"):
        method=getattr(ex,name,None)
        if not callable(method): continue
        try: orders=await method(symbol)
        except Exception: continue
        for order in orders or []:
            info=order.get("info") or {}
            seen=order.get("clientOrderId") or info.get("clientOrderId") or info.get("origClientOrderId")
            if str(seen or "")==client_order_id: return order
    return None


async def _ioc(ex, symbol: str, side: str, size: float, price: float, client_order_id: str | None = None) -> dict:
    params={"timeInForce":"IOC"}
    if client_order_id: params["clientOrderId"]=client_order_id
    try: order=await ex.create_order(symbol,"limit",side,size,price,params)
    except Exception as exc:
        if not client_order_id: raise
        order=await _find_client_order(ex,symbol,client_order_id)
        if order is None: raise LegFailure(f"ambiguous submit {client_order_id}; do not retry blindly") from exc
    if order.get("id") and (order.get("filled") is None or order.get("status") in (None,"open")):
        order=await ex.fetch_order(order["id"],symbol)
    return order


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
    """Cross-venue IOC coordination with durable reconciliation; never atomic."""
    def __init__(self, exchanges: dict, journal_path="cross_orders.sqlite3"):
        self.exs=exchanges
        self.ledger=OrderLedger(journal_path)

    @staticmethod
    def _filled(order): return float(order.get("filled") or 0.0)

    @staticmethod
    def _quote_fee(order,quote):
        fees=order.get("fees") or ([order.get("fee")] if order.get("fee") else [])
        return sum(float(f.get("cost") or 0) for f in fees if f and f.get("currency")==quote)

    async def _hedge(self,eid,o,eb,es,rb,rs,fb,fs):
        if fb>fs: ex,side,amount,levels=eb,"sell",fb-fs,"bids"
        else: ex,side,amount,levels=es,"buy",fs-fb,"asks"
        amount=float(ex.amount_to_precision(o.symbol,amount))
        book=await ex.fetch_order_book(o.symbol,10)
        depth=book.get(levels) or []
        if amount<=0 or not depth:
            self.ledger.set_state(eid,"RECONCILIATION_REQUIRED",{"reason":"no_hedge_depth"})
            raise LegFailure(f"{eid}: no safe hedge depth; reconcile")
        cid=f"nuarb-{eid[:16]}-h"
        self.ledger.add_leg(eid,{"key":"hedge","venue":ex.id,"client_order_id":cid,"side":side,"requested":amount})
        try: hedge=await _ioc(ex,o.symbol,side,amount,float(depth[0][0]),cid)
        except Exception as exc:
            self.ledger.update_leg(eid,"hedge",status="UNKNOWN",error=repr(exc))
            self.ledger.set_state(eid,"RECONCILIATION_REQUIRED",{"hedgeError":repr(exc)})
            raise LegFailure(f"{eid}: hedge unknown; reconcile") from exc
        self.ledger.update_leg(eid,"hedge",status="FILLED" if self._filled(hedge)+1e-12>=amount else "PARTIALLY_FILLED",order=hedge)
        if self._filled(hedge)+1e-12<amount:
            self.ledger.set_state(eid,"RECONCILIATION_REQUIRED",{"hedgeRequested":amount,"hedgeFilled":self._filled(hedge)})
            raise LegFailure(f"{eid}: incomplete hedge; reconcile")
        return hedge

    async def execute(self,o):
        if self.ledger.unresolved(): raise LegFailure("unresolved durable order exists; reconcile before new engagement")
        eb,es=self.exs[o.buy_ex],self.exs[o.sell_ex]
        size=min(float(eb.amount_to_precision(o.symbol,o.base)),float(es.amount_to_precision(o.symbol,o.base)))
        if size<=0: return TradeResult(0.0,ok=False)
        nonce=uuid.uuid4().hex
        buy_id,sell_id=f"nuarb-{nonce[:16]}-b",f"nuarb-{nonce[:16]}-s"
        eid=self.ledger.begin(o.symbol,[
            {"key":"buy","venue":o.buy_ex,"client_order_id":buy_id,"side":"buy","requested":size},
            {"key":"sell","venue":o.sell_ex,"client_order_id":sell_id,"side":"sell","requested":size}])
        results=await asyncio.gather(_ioc(eb,o.symbol,"buy",size,o.limit_buy,buy_id),
                                     _ioc(es,o.symbol,"sell",size,o.limit_sell,sell_id),return_exceptions=True)
        resolved,unknown={},[]
        for key,ex,cid,result in (("buy",eb,buy_id,results[0]),("sell",es,sell_id,results[1])):
            if isinstance(result,Exception):
                result=await _find_client_order(ex,o.symbol,cid)
                if result is None:
                    unknown.append({"leg":key,"clientOrderId":cid})
                    self.ledger.update_leg(eid,key,status="UNKNOWN",error=repr(results[0 if key=="buy" else 1]))
                    continue
            resolved[key]=result
            self.ledger.update_leg(eid,key,status=str(result.get("status") or "RESOLVED"),order=result)
        if unknown:
            self.ledger.set_state(eid,"RECONCILIATION_REQUIRED",{"unknown":unknown,"knownOrders":list(resolved)})
            raise LegFailure(f"{eid}: unknown order status; durable reconciliation required")
        rb,rs=resolved["buy"],resolved["sell"]
        fb,fs=self._filled(rb),self._filled(rs)
        if fb<=0 and fs<=0:
            self.ledger.set_state(eid,"NO_FILL")
            return TradeResult(0.0,ok=False)
        hedge=await self._hedge(eid,o,eb,es,rb,rs,fb,fs) if abs(fb-fs)>1e-12 else None
        cost=float(rb.get("cost") or fb*float(rb.get("average") or o.limit_buy))
        proceeds=float(rs.get("cost") or fs*float(rs.get("average") or o.limit_sell))
        fees=self._quote_fee(rb,o.quote_ccy)+self._quote_fee(rs,o.quote_ccy)
        pnl=proceeds-cost-fees
        if hedge is not None:
            hv=float(hedge.get("cost") or self._filled(hedge)*float(hedge.get("average") or 0))
            hf=self._quote_fee(hedge,o.quote_ccy)
            pnl += hv-hf if fb>fs else -hv-hf
        self.ledger.set_state(eid,"HEDGED" if hedge is not None else "COMPLETED",
                              {"buyFilled":fb,"sellFilled":fs,"hedgeFilled":self._filled(hedge) if hedge else 0,"pnl":pnl})
        return TradeResult(pnl)
