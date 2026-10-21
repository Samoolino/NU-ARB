import asyncio
from types import SimpleNamespace

import pytest

from arbx.execute import LegFailure, LiveExecutor


class TriExchange:
    id = "fake"

    def __init__(self, fees=None, *, fill_fraction=1.0, ambiguous=False):
        self.fees = fees or {}
        self.fill_fraction = fill_fraction
        self.ambiguous = ambiguous
        self.orders = []
        self.calls = 0
        self.markets = {
            "A/USDT": {"base": "A", "quote": "USDT"},
            "A/B": {"base": "A", "quote": "B"},
            "USDT/B": {"base": "USDT", "quote": "B"},
        }

    def market(self, symbol):
        return self.markets[symbol]

    def amount_to_precision(self, symbol, amount):
        return str(round(float(amount), 8))

    async def create_order(self, symbol, order_type, side, amount, price, params=None):
        self.calls += 1
        filled = float(amount) * self.fill_fraction
        order = {
            "id": f"order-{self.calls}",
            "clientOrderId": (params or {}).get("clientOrderId"),
            "symbol": symbol,
            "side": side,
            "filled": filled,
            "status": "closed",
            "cost": filled * float(price),
            "average": float(price),
            "fees": self.fees.get(symbol, [{"cost": 0.0, "currency": self.markets[symbol]["base" if side == "buy" else "quote"]}]),
        }
        self.orders.append(order)
        if self.ambiguous:
            self.ambiguous = False
            raise TimeoutError("accepted order but response lost")
        return order

    async def fetch_open_orders(self, symbol=None):
        return list(self.orders)

    async def fetch_orders(self, symbol=None):
        return list(self.orders)

    async def fetch_closed_orders(self, symbol=None):
        return list(self.orders)


def opportunity():
    return SimpleNamespace(
        start=100.0,
        legs=[
            SimpleNamespace(symbol="A/USDT", side="buy"),
            SimpleNamespace(symbol="A/B", side="sell"),
            SimpleNamespace(symbol="USDT/B", side="buy"),
        ],
        limits=[10.0, 2.0, 2.0],
    )


def test_triangular_execution_records_all_legs_and_complete_fee_evidence(tmp_path):
    ex = TriExchange(fees={
        "A/USDT": [{"cost": 0.1, "currency": "A"}],
        "A/B": [{"cost": 0.02, "currency": "B"}],
        "USDT/B": [{"cost": 0.01, "currency": "USDT"}],
    })
    executor = LiveExecutor(ex, None, journal_path=str(tmp_path / "tri.sqlite3"))
    result = asyncio.run(executor.execute_tri(opportunity()))
    assert result.final > 0
    assert result.pnl == pytest.approx(result.final - 100.0)
    assert executor.ledger.unresolved() == []
    executor.ledger.close()


def test_third_token_fee_requires_reconciliation_and_blocks_new_route(tmp_path):
    ex = TriExchange(fees={"A/USDT": [{"cost": 0.01, "currency": "BNB"}]})
    executor = LiveExecutor(ex, None, journal_path=str(tmp_path / "tri.sqlite3"))
    with pytest.raises(LegFailure, match="third-token fee"):
        asyncio.run(executor.execute_tri(opportunity()))
    assert executor.ledger.unresolved()
    calls = ex.calls
    with pytest.raises(LegFailure, match="unresolved triangular execution"):
        asyncio.run(executor.execute_tri(opportunity()))
    assert ex.calls == calls
    executor.ledger.close()


def test_empty_fee_array_without_trade_fee_lookup_fails_closed(tmp_path):
    ex = TriExchange(fees={"A/USDT": []})
    executor = LiveExecutor(ex, None, journal_path=str(tmp_path / "tri.sqlite3"))
    with pytest.raises(LegFailure, match="fee evidence missing"):
        asyncio.run(executor.execute_tri(opportunity()))
    assert executor.ledger.unresolved()
    executor.ledger.close()


def test_partial_triangular_fill_stops_route_and_persists_reconciliation(tmp_path):
    ex = TriExchange(fill_fraction=0.5)
    executor = LiveExecutor(ex, None, journal_path=str(tmp_path / "tri.sqlite3"))
    with pytest.raises(LegFailure, match="partial triangular fill"):
        asyncio.run(executor.execute_tri(opportunity()))
    assert ex.calls == 1
    assert executor.ledger.unresolved()
    executor.ledger.close()


def test_ambiguous_triangular_submit_is_resolved_without_resubmission(tmp_path):
    ex = TriExchange(ambiguous=True)
    executor = LiveExecutor(ex, None, journal_path=str(tmp_path / "tri.sqlite3"))
    result = asyncio.run(executor.execute_tri(opportunity()))
    assert ex.calls == 3
    assert result.final > 0
    assert executor.ledger.unresolved() == []
    executor.ledger.close()
