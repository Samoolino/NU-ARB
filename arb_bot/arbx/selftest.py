"""Offline self-test: fake exchanges with injected triangular + cross-exchange edges. No network, no keys.
Proves the install works and exercises stream -> evaluate -> gate -> paper-execute -> verify -> journal."""
from __future__ import annotations

import asyncio
import os
import random
import time
from pathlib import Path

from arbx import worker as worker_mod
from arbx.config import Config, ExchangeCfg
from arbx.gate import Decision, ProfitGate, RiskManager
from arbx.hub import Hub
from arbx.journal import TradeJournal
from arbx.execute import TradeResult
from arbx.strategy import CrossOpp, Opp
from arbx.util import buy_with_quote, loop_factory, walk_base


def _mk(sym, base, quote):
    return {"symbol": sym, "base": base, "quote": quote, "spot": True, "active": True, "taker": 0.001,
            "limits": {"amount": {"min": None}, "cost": {"min": None}}}


class FakeExchange:
    id = "fake"
    MID = {"BTC/USDT": 60000.0, "TRX/USDT": 0.12, "TRX/BTC": 2e-6}

    def __init__(self, bias=1.0, seed=1):
        self.rng, self.bias, self.cnt = random.Random(seed), bias, {}
        self.markets = {"BTC/USDT": _mk("BTC/USDT", "BTC", "USDT"), "TRX/USDT": _mk("TRX/USDT", "TRX", "USDT"),
                        "TRX/BTC": _mk("TRX/BTC", "TRX", "BTC")}
        self.currencies = None

    async def load_markets(self): return self.markets
    async def fetch_time(self): await asyncio.sleep(0.002); return int(time.time() * 1000)

    async def fetch_tickers(self):
        return {"BTC/USDT": {"last": 60000, "quoteVolume": 5e7}, "TRX/USDT": {"last": 0.12, "quoteVolume": 5e7},
                "TRX/BTC": {"last": 2e-6, "quoteVolume": 800}}

    async def watch_order_book(self, symbol, limit=None):
        await asyncio.sleep(0.004 + self.rng.random() * 0.006)
        self.cnt[symbol] = self.cnt.get(symbol, 0) + 1
        mid = self.MID[symbol]
        if symbol.endswith("/USDT"):
            mid *= self.bias                                   # consistent shift -> cross edge, no triangle edge
        if symbol == "TRX/BTC" and self.cnt[symbol] % 25 == 0:
            mid *= 1.006                                       # injected triangular dislocation
        usd_per_quote = 60000.0 if symbol.endswith("/BTC") else 1.0
        size = 5000.0 / (mid * usd_per_quote)
        h = mid * 0.00005
        return {"bids": [[mid - h - mid * 2e-5 * i, size] for i in range(5)],
                "asks": [[mid + h + mid * 2e-5 * i, size] for i in range(5)]}

    def price_to_precision(self, s, p): return f"{p:.8g}"
    def amount_to_precision(self, s, a): return f"{a:.6f}"
    def market(self, s): return self.markets[s]
    async def close(self): pass


def _unit_checks() -> None:
    asks = [[100.0, 1.0], [101.0, 2.0]]
    base, last = buy_with_quote(asks, 200.0)
    assert abs(base - (1.0 + 100.0 / 101.0)) < 1e-9 and last == 101.0
    assert buy_with_quote(asks, 10_000.0) is None                      # insufficient depth
    q, last = walk_base([[10.0, 1.0], [9.0, 5.0]], 3.0)
    assert abs(q - 28.0) < 1e-9 and last == 9.0

    cfg = Config()
    risk = RiskManager(cfg)
    gate = ProfitGate(cfg, risk)
    leg = type("L", (), {"symbol": "X/USDT", "side": "buy"})()
    def opp(**kw):
        d = dict(name="t", start_asset="USDT", start=25.0, expected_final=25.05, worst_final=25.03, net_bps=20.0,
                 worst_bps=12.0, age_ms=10.0, legs=(leg,), limits=(1.0,), path=((25.0, 25.0),))
        d.update(kw)
        return Opp(**d)
    assert gate.check_tri(opp(), {}, True, 100.0).ok
    assert gate.check_tri(opp(age_ms=999), {}, True, 100.0).reason == "stale_book"
    assert gate.check_tri(opp(worst_bps=0.1), {}, True, 100.0).reason == "worst_case_below_floor"
    assert gate.check_tri(opp(worst_final=25.001), {}, True, 100.0).reason == "profit_below_min_usd"
    assert gate.check_tri(opp(), {}, False, 100.0).reason == "latency_degraded"
    assert gate.check_tri(opp(), {}, True, 1.0).reason == "insufficient_balance"
    assert gate.check_tri(opp(), {"X/USDT": (None, 50.0)}, True, 100.0).reason == "below_exchange_minimum"
    def cross_opp(**kw):
        d = dict(symbol="BTC/USDT", buy_ex="a", sell_ex="b", base=0.001, limit_buy=25_000.0,
                 limit_sell=25_100.0, cost=25.0, expected_usd=0.10, worst_usd=0.02,
                 net_bps=20.0, worst_bps=12.0, age_ms=10.0, base_ccy="BTC", quote_ccy="USDT")
        d.update(kw)
        return CrossOpp(**d)
    assert gate.check_cross(cross_opp(), {}, {}, True, 100.0, 1.0).ok
    assert gate.check_cross(cross_opp(worst_bps=0.1), {}, {}, True, 100.0, 1.0).reason == "worst_case_below_floor"
    assert gate.check_cross(cross_opp(worst_usd=0.001), {}, {}, True, 100.0, 1.0).reason == "profit_below_min_usd"
    assert gate.check_cross(cross_opp(), {}, {}, True, 1.0, 1.0).reason == "insufficient_inventory"
    risk.halt("x")
    assert gate.check_tri(opp(), {}, True, 100.0).reason == "halted"
    journal_path = Path("selftest_durable_journal.sqlite3")
    journal_path.unlink(missing_ok=True)
    durable = TradeJournal(journal_path)
    durable.append({"session_id": "test-session", "mode": "paper", "exchange_a": "a",
                    "strategy": "cross_exchange", "net_pnl": 0.12, "execution_status": "FILLED",
                    "verification_status": "PAPER_SIMULATION", "cumulative_realized_pnl": 0.12})
    assert abs(durable.realized("test-session") - 0.12) < 1e-9
    durable.close()
    reopened = TradeJournal(journal_path)
    assert abs(reopened.realized("test-session") - 0.12) < 1e-9
    reopened.close()
    journal_path.unlink(missing_ok=True)
    Path(str(journal_path) + "-wal").unlink(missing_ok=True)
    Path(str(journal_path) + "-shm").unlink(missing_ok=True)
    target_csv = Path("selftest_target.csv")
    target_csv.unlink(missing_ok=True)
    target_cfg = Config(exchanges=[ExchangeCfg("target_test")], journal_path=target_csv, target_profit_usd=0.05)
    target_hub = Hub(target_cfg)
    target_hub.settle("target_test", "X BTC/USDT target_test>target_test2", 25.0,
                      TradeResult(0.06), 0.0, 12.0)
    assert target_hub.risk.halted and "target attained" in target_hub.risk.reason
    assert target_hub.stats.target_progress_pct == 100.0
    resumed_session = target_hub.session_id
    target_hub.journal_store.close()
    previous_session = os.environ.get("BOT_SESSION_ID")
    os.environ["BOT_SESSION_ID"] = resumed_session
    resumed_hub = Hub(target_cfg)
    assert resumed_hub.risk.halted and resumed_hub.stats.target_progress_pct == 100.0
    resumed_hub.journal_store.close()
    if previous_session is None:
        os.environ.pop("BOT_SESSION_ID", None)
    else:
        os.environ["BOT_SESSION_ID"] = previous_session
    target_db = target_csv.with_suffix(".sqlite3")
    target_db.unlink(missing_ok=True)
    Path(str(target_db) + "-wal").unlink(missing_ok=True)
    Path(str(target_db) + "-shm").unlink(missing_ok=True)
    target_csv.unlink(missing_ok=True)
    print("unit checks: OK (depth walking, risk gates, durable journal, target resume)")


def run_selftest(seconds: float = 6.0) -> bool:
    _unit_checks()
    journal = Path("selftest_journal.csv")
    journal.unlink(missing_ok=True)
    journal.with_suffix(".sqlite3").unlink(missing_ok=True)
    cfg = Config(mode="paper", exchanges=[ExchangeCfg("fake_a"), ExchangeCfg("fake_b")],
                 journal_path=journal, cooldown_s=0.05, max_loss_usd=50.0, cross_top_n=5)
    cfg.validate()
    original = worker_mod.build_exchange
    # The paper starter notional is $3; a 0.4% venue shift is still below the
    # configured $0.01 minimum after fees at that size. Keep the fake edge large
    # enough to exercise successful paper settlement without weakening any gate.
    worker_mod.build_exchange = lambda x, live: FakeExchange(bias=1.01 if x.id == "fake_b" else 1.0,
                                                             seed=1 if x.id == "fake_a" else 2)

    async def go():
        hub = Hub(cfg)
        t = asyncio.create_task(hub.run())
        await asyncio.sleep(seconds)
        hub.request_stop()
        await t
        return hub

    try:
        hub = asyncio.run(go(), loop_factory=loop_factory())
    finally:
        worker_mod.build_exchange = original
    db_path = journal.with_suffix(".sqlite3")
    db_path.unlink(missing_ok=True)
    Path(str(db_path) + "-wal").unlink(missing_ok=True)
    Path(str(db_path) + "-shm").unlink(missing_ok=True)
    s = hub.stats
    rows = journal.read_text().count("\n") - 1 if journal.exists() else 0
    print(f"paper run {seconds:.0f}s: scans={s.scans} signals={s.signals} trades={s.trades} pnl={s.pnl:+.4f} "
          f"rejects={dict(s.rejects)} journal_rows={rows} halted={hub.risk.halted}")
    ok = s.trades > 0 and not hub.risk.halted and s.pnl > 0 and rows == s.trades + s.failed
    print("SELFTEST PASSED" if ok else "SELFTEST FAILED")
    return ok
