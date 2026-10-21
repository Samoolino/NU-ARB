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
    """Sequential IOC triangular execution with durable intent and fee reconciliation."""

    def __init__(self, ex, cfg, journal_path="tri_orders.sqlite3"):
        self.ex, self.cfg = ex, cfg
        self.ledger = OrderLedger(journal_path)

    async def _ensure_tri_fee_data(self, symbol: str, order: dict) -> None:
        # An empty fee array is not evidence that a filled order incurred zero fees.
        fees = order.get("fees")
        if fees:
            if all(f and f.get("cost") is not None and f.get("currency") for f in fees):
                return
            raise LegFailure("incomplete triangular fee record")
        fee = order.get("fee")
        if fee and fee.get("cost") is not None and fee.get("currency"):
            order["fees"] = [fee]
            return
        order_id = order.get("id")
        method = getattr(self.ex, "fetch_my_trades", None)
        if not order_id or not callable(method):
            raise LegFailure("triangular fee evidence missing; cannot verify realized PnL")
        try:
            trades = await method(symbol, None, None, {"orderId": str(order_id)})
        except Exception as exc:
            raise LegFailure("triangular fee lookup failed; realized PnL is unknown") from exc
        matched = []
        for trade in trades or []:
            info = trade.get("info") or {}
            trade_order_id = trade.get("order") or info.get("orderId") or info.get("order_id")
            if str(trade_order_id or "") != str(order_id):
                continue
            if trade.get("fees") is not None:
                matched.extend(trade.get("fees") or [])
            elif trade.get("fee") is not None:
                matched.append(trade["fee"])
            else:
                raise LegFailure("triangular fill has no fee evidence")
        if not matched or any(not f or f.get("cost") is None or not f.get("currency") for f in matched):
            raise LegFailure("no complete triangular fee evidence found")
        order["fees"] = matched

    @staticmethod
    def _tri_net_received(order: dict, side: str, market: dict) -> float:
        filled = float(order.get("filled") or 0.0)
        if filled <= 0:
            return 0.0
        average = float(order.get("average") or (
            float(order.get("cost") or 0.0) / filled if order.get("cost") is not None else 0.0
        ) or order.get("price") or 0.0)
        if average <= 0:
            raise LegFailure("triangular fill lacks a valid average price")
        if side == "buy":
            received_ccy, input_ccy = market["base"], market["quote"]
            received = filled
        else:
            received_ccy, input_ccy = market["quote"], market["base"]
            received = float(order.get("cost") or filled * average)
        fees = order.get("fees") if "fees" in order else ([order["fee"]] if order.get("fee") else None)
        if not fees:
            raise LegFailure("triangular fee evidence missing")
        for fee in fees:
            if not fee or fee.get("cost") is None or not fee.get("currency"):
                raise LegFailure("incomplete triangular fee record")
            fee_amount = float(fee["cost"])
            if fee_amount < 0:
                raise LegFailure("negative fee record is invalid")
            currency = fee["currency"]
            if currency == received_ccy:
                received -= fee_amount
            elif currency == input_ccy:
                # Convert input-denominated fees into output units at the actual fill average.
                received -= fee_amount / average if side == "buy" else fee_amount * average
            else:
                raise LegFailure(
                    f"third-token fee {currency} cannot be valued safely in the route; reconciliation required"
                )
        if received < -1e-12:
            raise LegFailure("fees exceed received triangular output")
        return max(0.0, received)

    async def execute_tri(self, o) -> TradeResult:
        if self.ledger.unresolved():
            raise LegFailure("unresolved triangular execution exists; reconcile before new engagement")
        amount, done = o.start, []
        nonce = uuid.uuid4().hex
        intents = []
        for index, (leg, lim) in enumerate(zip(o.legs, o.limits)):
            raw = amount / lim if leg.side == "buy" else amount
            size = float(self.ex.amount_to_precision(leg.symbol, raw))
            intents.append({
                "key": f"leg{index + 1}", "venue": getattr(self.ex, "id", "venue"),
                "client_order_id": f"nuarb-{nonce[:12]}-{index + 1}",
                "side": leg.side, "requested": max(0.0, size),
            })
        try:
            eid = self.ledger.begin("triangular", intents)
        except Exception as exc:
            raise LegFailure("could not create durable triangular order intent") from exc

        for index, (leg, lim, intent) in enumerate(zip(o.legs, o.limits, intents)):
            m = self.ex.market(leg.symbol)
            raw = amount / lim if leg.side == "buy" else amount
            size = float(self.ex.amount_to_precision(leg.symbol, raw))
            try:
                if size <= 0:
                    raise NoFill("size rounds to zero")
                order = await _ioc(self.ex, leg.symbol, leg.side, size, lim, intent["client_order_id"])
                filled = float(order.get("filled") or 0.0)
                self.ledger.update_leg(
                    eid, intent["key"],
                    status=("FILLED" if filled + 1e-12 >= size else "PARTIALLY_FILLED" if filled > 0 else "NO_FILL"),
                    order=order,
                )
                if filled <= 0:
                    raise NoFill(f"{leg.symbol} {leg.side} unfilled")
                if filled + 1e-12 < size:
                    self.ledger.set_state(eid, "RECONCILIATION_REQUIRED", {
                        "reason": "partial_triangular_fill", "leg": intent["key"],
                        "requested": size, "filled": filled, "order_id": order.get("id")
                    })
                    raise LegFailure("partial triangular fill; stop route and reconcile inventory")
                await self._ensure_tri_fee_data(leg.symbol, order)
                got = self._tri_net_received(order, leg.side, m)
                if got <= 0:
                    raise LegFailure(f"{leg.symbol} {leg.side} net output is zero")
            except NoFill as exc:
                if not done and index == 0:
                    self.ledger.set_state(eid, "NO_FILL", {"reason": str(exc)})
                    return TradeResult(0.0, 0.0, ok=False)
                if done:
                    try:
                        await self._unwind(done)
                    except Exception as unwind_exc:
                        self.ledger.set_state(eid, "RECONCILIATION_REQUIRED", {
                            "reason": str(exc), "unwindError": repr(unwind_exc)
                        })
                        raise LegFailure(f"{eid}: unwind failed; manual reconciliation required") from unwind_exc
                self.ledger.set_state(eid, "RECONCILIATION_REQUIRED", {"reason": str(exc), "completedLegs": len(done)})
                raise LegFailure(f"{eid}: missed {leg.symbol} {leg.side}; reconciliation required") from exc
            except Exception as exc:
                unwind_error = None
                if done:
                    try:
                        await self._unwind(done)
                    except Exception as unwind_exc:
                        unwind_error = repr(unwind_exc)
                self.ledger.set_state(eid, "RECONCILIATION_REQUIRED", {
                    "reason": repr(exc), "completedLegs": len(done), "unwindError": unwind_error
                })
                raise LegFailure(f"{eid}: {leg.symbol} {leg.side}: {exc!r}; manual reconciliation required") from exc
            done.append((leg, m, amount, got))
            amount = got

        pnl = amount - o.start
        self.ledger.set_state(eid, "COMPLETED", {
            "start": o.start, "final": amount, "pnl": pnl, "legs": len(done)
        })
        return TradeResult(pnl, amount)

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

    async def _ensure_fee_data(self, ex, symbol, order):
        if "fees" in order or order.get("fee") is not None:
            return
        order_id = order.get("id")
        method = getattr(ex, "fetch_my_trades", None)
        if not order_id or not callable(method):
            raise LegFailure("exchange did not return fees and cannot fetch user trades")
        try:
            trades = await method(symbol, None, None, {"orderId": str(order_id)})
        except Exception as exc:
            raise LegFailure("fee lookup failed; realized profitability cannot be verified") from exc
        matched = []
        for trade in trades or []:
            info = trade.get("info") or {}
            trade_order_id = trade.get("order") or info.get("orderId") or info.get("order_id")
            if str(trade_order_id or "") != str(order_id):
                continue
            if trade.get("fees") is not None:
                matched.extend(trade.get("fees") or [])
            elif trade.get("fee") is not None:
                matched.append(trade["fee"])
            else:
                raise LegFailure("trade exists but fee evidence is missing")
        if not matched:
            raise LegFailure("no matching user-trade fee evidence found")
        order["fees"] = matched

    @staticmethod
    def _quote_fee(order, quote, base, average_price):
        if "fees" not in order and order.get("fee") is None:
            raise LegFailure("fee data missing; refusing to claim realized profit")
        fees = order.get("fees") if "fees" in order else [order.get("fee")]
        total = 0.0
        for fee in fees or []:
            if not fee or fee.get("cost") is None or not fee.get("currency"):
                raise LegFailure("incomplete fee record; realized profitability is unknown")
            amount = float(fee["cost"])
            if fee["currency"] == quote:
                total += amount
            elif fee["currency"] == base:
                total += amount * average_price
            else:
                raise LegFailure(f"fee currency {fee['currency']} cannot be valued in {quote}")
        return total

    async def _hedge(self,eid,o,eb,es,rb,rs,fb,fs):
        if fb>fs: ex,side,amount,levels=eb,"sell",fb-fs,"bids"
        else: ex,side,amount,levels=es,"buy",fs-fb,"asks"
        amount=float(ex.amount_to_precision(o.symbol,amount))
        try:
            book=await ex.fetch_order_book(o.symbol,10)
        except Exception as exc:
            self.ledger.set_state(eid,"RECONCILIATION_REQUIRED",{"reason":"hedge_book_failed","error":repr(exc)})
            raise LegFailure(f"{eid}: hedge book unavailable; reconciliation required") from exc
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
        buy_avg=float(rb.get("average") or (cost/fb if fb else o.limit_buy))
        sell_avg=float(rs.get("average") or (proceeds/fs if fs else o.limit_sell))
        try:
            await self._ensure_fee_data(eb,o.symbol,rb)
            await self._ensure_fee_data(es,o.symbol,rs)
            if hedge is not None:
                hedge_ex = eb if fb > fs else es
                await self._ensure_fee_data(hedge_ex,o.symbol,hedge)
            fees=self._quote_fee(rb,o.quote_ccy,o.base_ccy,buy_avg)+self._quote_fee(rs,o.quote_ccy,o.base_ccy,sell_avg)
            pnl=proceeds-cost-fees
            if hedge is not None:
                hv=float(hedge.get("cost") or self._filled(hedge)*float(hedge.get("average") or 0))
                hedge_avg=float(hedge.get("average") or (hv/self._filled(hedge) if self._filled(hedge) else 0.0))
                hf=self._quote_fee(hedge,o.quote_ccy,o.base_ccy,hedge_avg)
                pnl += hv-hf if fb>fs else -hv-hf
        except Exception as exc:
            self.ledger.set_state(eid,"RECONCILIATION_REQUIRED",{"feeError":repr(exc),"buyFilled":fb,"sellFilled":fs})
            raise LegFailure(f"{eid}: fee/PnL reconciliation required") from exc
        self.ledger.set_state(eid,"HEDGED" if hedge is not None else "COMPLETED",
                              {"buyFilled":fb,"sellFilled":fs,"hedgeFilled":self._filled(hedge) if hedge else 0,"pnl":pnl})
        return TradeResult(pnl)
