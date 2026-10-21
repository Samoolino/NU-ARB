import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from arbx.execute import CrossExecutor, LegFailure
from arbx.strategy import CrossOpp


def opp():
    return CrossOpp(
        symbol="BTC/USDT", buy_ex="buy", sell_ex="sell", base=1.0,
        limit_buy=100.0, limit_sell=102.0, cost=100.0,
        expected_usd=2.0, worst_usd=1.0, net_bps=200.0,
        worst_bps=100.0, age_ms=1.0, base_ccy="BTC", quote_ccy="USDT",
    )


class FakeExchange:
    def __init__(self, venue, *, fill=1.0, ambiguous=False, expose_after_timeout=True, include_fees=True):
        self.id=venue
        self.fill=fill
        self.ambiguous=ambiguous
        self.expose_after_timeout=expose_after_timeout
        self.include_fees=include_fees
        self.orders=[]
        self.calls=0

    def amount_to_precision(self, symbol, amount):
        return str(amount)

    async def create_order(self, symbol, order_type, side, amount, price, params):
        self.calls += 1
        order={"id":f"{self.id}-{self.calls}","clientOrderId":params.get("clientOrderId"),
               "symbol":symbol,"side":side,"filled":min(amount,self.fill),"status":"closed",
               "cost":min(amount,self.fill)*price,"average":price}
        if self.include_fees:
            order["fees"]=[]
        if self.expose_after_timeout or not self.ambiguous:
            self.orders.append(order)
        if self.ambiguous:
            self.ambiguous=False
            raise TimeoutError("response lost after accepted order")
        return order

    async def fetch_open_orders(self, symbol=None):
        return list(self.orders)

    async def fetch_orders(self, symbol=None):
        return list(self.orders)

    async def fetch_closed_orders(self, symbol=None):
        return list(self.orders)

    async def fetch_order_book(self, symbol, limit=10):
        return {"bids":[[99.0,10.0]],"asks":[[101.0,10.0]]}


def run(path, buy, sell):
    executor=CrossExecutor({"buy":buy,"sell":sell},journal_path=path)
    return executor


def test_balanced_cross_execution_is_journaled(tmp_path):
    buy,sell=FakeExchange("buy"),FakeExchange("sell")
    executor=run(tmp_path/"orders.sqlite3",buy,sell)
    result=asyncio.run(executor.execute(opp()))
    assert result.ok and result.pnl > 0
    assert not executor.ledger.unresolved()
    executor.ledger.close()


def test_ambiguous_submit_is_resolved_by_client_order_id_without_resubmit(tmp_path):
    buy,sell=FakeExchange("buy",ambiguous=True),FakeExchange("sell")
    executor=run(tmp_path/"orders.sqlite3",buy,sell)
    result=asyncio.run(executor.execute(opp()))
    assert result.ok
    assert buy.calls == 1 and sell.calls == 1
    assert not executor.ledger.unresolved()
    executor.ledger.close()


def test_unresolved_submit_persists_and_blocks_next_execution(tmp_path):
    buy,sell=FakeExchange("buy",ambiguous=True,expose_after_timeout=False),FakeExchange("sell")
    executor=run(tmp_path/"orders.sqlite3",buy,sell)
    with pytest.raises(LegFailure,match="unknown order status"):
        asyncio.run(executor.execute(opp()))
    assert executor.ledger.unresolved()
    calls=(buy.calls,sell.calls)
    with pytest.raises(LegFailure,match="unresolved durable order"):
        asyncio.run(executor.execute(opp()))
    assert (buy.calls,sell.calls)==calls
    executor.ledger.close()


def test_fee_paid_in_base_asset_is_valued_in_quote():
    assert CrossExecutor._quote_fee({"fees":[{"currency":"BTC","cost":0.01}]},
                                    "USDT","BTC",100.0) == 1.0


def test_unpriced_third_token_fee_fails_closed():
    with pytest.raises(LegFailure, match="cannot be valued"):
        CrossExecutor._quote_fee({"fees":[{"currency":"BNB","cost":0.01}]},
                                 "USDT","BTC",100.0)


def test_missing_fee_evidence_blocks_claiming_profit(tmp_path):
    buy,sell=FakeExchange("buy",include_fees=False),FakeExchange("sell",include_fees=False)
    executor=run(tmp_path/"orders.sqlite3",buy,sell)
    with pytest.raises(LegFailure,match="fee/PnL reconciliation required"):
        asyncio.run(executor.execute(opp()))
    assert executor.ledger.unresolved()
    executor.ledger.close()
