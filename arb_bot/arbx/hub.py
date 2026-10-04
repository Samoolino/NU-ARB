"""Hub: owns risk, gate, stats, journal; runs all workers; scans cross-exchange spreads."""
from __future__ import annotations

import asyncio
import math
import os
import queue
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field

from arbx.config import Config
from arbx.execute import CrossExecutor, LegFailure, PaperExecutor
from arbx.gate import ProfitGate, RiskManager
from arbx.journal import TradeJournal
from arbx.strategy import evaluate_cross
from arbx.worker import ExchangeWorker


@dataclass
class Stats:
    session_id: str = ""
    status: str = "starting"
    scans: int = 0
    signals: int = 0
    trades: int = 0
    failed: int = 0
    pnl: float = 0.0
    equity: float = 0.0
    target_profit_usd: float | None = None
    target_progress_pct: float = 0.0
    rejects: Counter = field(default_factory=Counter)
    rtt: dict = field(default_factory=dict)

    def snapshot(self) -> dict:
        return {"status": self.status, "session_id": self.session_id,
                "scans": self.scans, "signals": self.signals, "trades": self.trades,
                "failed": self.failed, "pnl": self.pnl, "equity": self.equity,
                "target_profit_usd": self.target_profit_usd, "target_progress_pct": self.target_progress_pct,
                "rejects": dict(self.rejects.most_common(4)), "rtt": dict(self.rtt)}


class Hub:
    def __init__(self, cfg: Config, out: "queue.Queue | None" = None):
        self.cfg, self.out = cfg, out
        self.session_id = os.getenv("BOT_SESSION_ID") or uuid.uuid4().hex
        self.journal_store = TradeJournal(cfg.journal_path.with_suffix(".sqlite3"))
        self.risk = RiskManager(cfg)
        self.risk.pnl = self.journal_store.realized(self.session_id)
        self.gate = ProfitGate(cfg, self.risk)
        self.stats = Stats(session_id=self.session_id, pnl=self.risk.pnl, equity=cfg.start_capital_usd + self.risk.pnl,
                           target_profit_usd=cfg.target_profit_usd)
        if cfg.target_profit_usd is not None:
            self.stats.target_progress_pct = min(100.0, max(0.0, self.risk.pnl / cfg.target_profit_usd * 100.0))
            if self.risk.pnl >= cfg.target_profit_usd:
                self.risk.halt(f"target attained: realized net PnL {self.risk.pnl:.4f} USD >= {cfg.target_profit_usd:.4f} USD")
        self.workers: list[ExchangeWorker] = []
        self.cross_syms: set = set()
        self.cross_exec = None
        self._opportunity_last_written: dict[str, float] = {}
        self._paper = PaperExecutor(cfg)
        self._loop = self._stop = None
        self._t_print = 0.0

    # ---- utilities ------------------------------------------------------
    @property
    def equity(self) -> float:
        return self.cfg.start_capital_usd + self.risk.pnl

    def trade_size(self) -> float:
        return self.cfg.trade_size_usd if self.cfg.mode == "live" else min(self.cfg.trade_size_usd, self.equity)

    def log(self, kind: str, msg: str) -> None:
        if self.out is not None:
            self.out.put(("log", kind, msg))
        else:
            print(f"[{time.strftime('%H:%M:%S')}] [{kind.upper()}] {msg}", flush=True)

    def request_stop(self) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)

    def journal(self, ex, name, start, pnl, worst_bps, ok, *, latency_ms=None, target_before=None) -> None:
        p = self.cfg.journal_path
        new = not p.exists()
        with p.open("a", newline="") as f:
            import csv
            w = csv.writer(f)
            if new:
                w.writerow(["ts", "mode", "exchange", "path", "start_usd", "pnl_usd", "guaranteed_bps", "ok"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), self.cfg.mode, ex, name, round(start, 6),
                        round(pnl, 6), round(worst_bps, 3), ok])
        strategy = "cross_exchange" if name.startswith("X ") else "triangular"
        symbols = [part for part in name.replace("X ", "").split() if "/" in part]
        self.journal_store.append({
            "session_id": self.session_id,
            "mode": self.cfg.mode,
            "exchange_a": str(ex).split("/")[0],
            "exchange_b": str(ex).split("/")[1] if "/" in str(ex) else None,
            "strategy": strategy,
            "path": [name],
            "symbols": symbols,
            "requested_quantity": start,
            "executed_quantity": None,
            "average_fill_price": None,
            "fees": None,
            "gross_pnl": None,
            "slippage": None,
            "net_pnl": pnl if ok else 0.0,
            "latency_ms": latency_ms,
            "book_age_ms": None,
            "execution_status": "FILLED" if ok else "UNFILLED_OR_FAILED",
            "verification_status": "PAPER_SIMULATION" if self.cfg.mode == "paper" and ok else "FILL_RESULT_RECORDED" if ok else "NOT_FILLED",
            "risk_decision": "APPROVED" if ok else "REJECTED_OR_FAILED",
            "failure_reason": None if ok else "Order was not completed; inspect engine log for cause",
            "target_before": target_before,
            "target_after": self.risk.pnl,
            "cumulative_realized_pnl": self.risk.pnl,
        })

    def settle(self, ex, name, start, res, worst_bps, ms) -> None:
        """Post-trade verification: realized result must respect the pre-trade guarantee."""
        st = self.stats
        target_before = self.risk.pnl
        self.risk.record(res.pnl, res.ok)
        st.pnl, st.equity = self.risk.pnl, self.equity
        if self.cfg.target_profit_usd:
            st.target_progress_pct = min(100.0, max(0.0, st.pnl / self.cfg.target_profit_usd * 100.0))
        if res.ok:
            st.trades += 1
            realized = res.pnl / start * 1e4
            if realized < worst_bps - self.cfg.verify_slack_bps:
                self.risk.halt(f"ASSURANCE VIOLATED on {name}: realized {realized:.2f}bps < guaranteed "
                               f"{worst_bps:.2f}bps (wrong fee tier? tick rounding? partial fills?)")
            self.log("trade", f"[{ex}] {name} pnl={res.pnl:+.5f} ({realized:+.1f}bps, floor {worst_bps:+.1f}) {ms:.0f}ms")
        else:
            st.failed += 1
            self.log("warn", f"[{ex}] {name} unfilled (IOC expired) - no position taken")
        self.journal(ex, name, start, res.pnl, worst_bps, res.ok, latency_ms=ms, target_before=target_before)
        target_profit = self.cfg.target_profit_usd
        if target_profit is not None and st.pnl >= target_profit:
            self.risk.halt(f"target attained: realized net PnL {st.pnl:.4f} USD >= {target_profit:.4f} USD")
        tgt = self.cfg.target_equity_usd
        if tgt and st.equity >= tgt:
            self.risk.halt(f"target equity {tgt} reached (funds remain on the exchange)")

    def record_opportunity(self, *, strategy: str, exchange_a: str, exchange_b: str | None,
                           symbol: str, requested_usd: float, expected_net_usd: float,
                           worst_case_net_usd: float, expected_net_bps: float,
                           worst_case_net_bps: float, book_age_ms: float, approved: bool,
                           rejection_reason: str | None, evidence: dict) -> None:
        key = f"{strategy}:{exchange_a}:{exchange_b or ''}:{symbol}"
        now = time.monotonic()
        if now - self._opportunity_last_written.get(key, 0.0) < 1.0:
            return
        self._opportunity_last_written[key] = now
        self.journal_store.append_opportunity({
            "session_id": self.session_id,
            "mode": self.cfg.mode,
            "strategy": strategy,
            "exchange_a": exchange_a,
            "exchange_b": exchange_b,
            "symbol": symbol,
            "requested_usd": requested_usd,
            "expected_net_usd": expected_net_usd,
            "worst_case_net_usd": worst_case_net_usd,
            "expected_net_bps": expected_net_bps,
            "worst_case_net_bps": worst_case_net_bps,
            "book_age_ms": book_age_ms,
            "decision": "APPROVED_BY_GATE" if approved else "REJECTED_BY_GATE",
            "rejection_reason": rejection_reason,
            "evidence": evidence,
        })

    # ---- cross-exchange ---------------------------------------------------
    @property
    def cross_active(self) -> bool:
        return (self.cfg.cross_enabled and len(self.workers) >= 2
                and (self.cfg.mode == "paper" or self.cfg.cross_live))

    def _pick_cross_symbols(self) -> set:
        counts: dict = {}
        for w in self.workers:
            for s, v in w.cross_candidates().items():
                counts.setdefault(s, []).append(v)
        common = sorted(((min(v), s) for s, v in counts.items() if len(v) >= 2), reverse=True)
        return {s for _, s in common[: self.cfg.cross_top_n]}

    async def scan_cross(self, w, dirty) -> None:
        cfg, now, size, best = self.cfg, time.monotonic(), self.trade_size(), None
        for sym in dirty & self.cross_syms:
            b1 = w.md.books.get(sym)
            if not b1:
                continue
            for o in self.workers:
                b2 = o.md.books.get(sym) if o is not w else None
                if not b2:
                    continue
                for bw, sw, bb, sb in ((w, o, b1, b2), (o, w, b2, b1)):
                    x = evaluate_cross(sym, bw, sw, bb, sb, size, cfg, now)
                    if x and (best is None or x.worst_bps > best[0].worst_bps):
                        best = (x, bw, sw)
        if best:
            await self._fire_cross(*best)

    async def _fire_cross(self, x, bw, sw) -> None:
        self.stats.signals += 1
        if bw.lock.locked() or sw.lock.locked():
            return
        live = self.cfg.mode == "live"
        first, second = sorted((bw, sw), key=lambda w: w.id)
        async with first.lock, second.lock:
            if live:
                results = await asyncio.gather(bw.refresh_balance(), sw.refresh_balance(), return_exceptions=True)
                errors = [result for result in results if isinstance(result, Exception)]
                if errors:
                    self.risk.halt(
                        f"cross-venue balance refresh failed before order ({type(errors[0]).__name__})"
                    )
                    return
            buy_book = bw.md.books.get(x.symbol)
            sell_book = sw.md.books.get(x.symbol)
            if buy_book is None or sell_book is None:
                self.stats.rejects["missing_order_book"] += 1
                return
            refreshed = evaluate_cross(x.symbol, bw, sw, buy_book, sell_book,
                                       self.trade_size(), self.cfg, time.monotonic())
            if refreshed is None:
                self.stats.rejects["opportunity_disappeared"] += 1
                return
            x = refreshed
            d = self.gate.check_cross(x, bw.mlimits, sw.mlimits, bw.lat.ok() and sw.lat.ok(),
                                      bw.free_of(x.quote_ccy), sw.free.get(x.base_ccy, 0.0) if live else math.inf)
            self.record_opportunity(
                strategy="cross_exchange", exchange_a=x.buy_ex, exchange_b=x.sell_ex, symbol=x.symbol,
                requested_usd=x.cost, expected_net_usd=x.expected_usd, worst_case_net_usd=x.worst_usd,
                expected_net_bps=x.net_bps, worst_case_net_bps=x.worst_bps, book_age_ms=x.age_ms,
                approved=d.ok, rejection_reason=None if d.ok else d.reason,
                evidence={
                    "baseQuantity": x.base, "limitBuy": x.limit_buy, "limitSell": x.limit_sell,
                    "buyRestLatency": bw.lat.stats(), "sellRestLatency": sw.lat.stats(),
                    "buyBookSequence": buy_book.sequence, "sellBookSequence": sell_book.sequence,
                    "buyBookExchangeTimestamp": buy_book.timestamp_exchange,
                    "sellBookExchangeTimestamp": sell_book.timestamp_exchange,
                    "rebalanceHaircutBps": self.cfg.rebalance_haircut_bps,
                },
            )
            if not d.ok:
                self.stats.rejects[d.reason] += 1
                return
            t0, name = time.perf_counter(), f"X {x.symbol} {x.buy_ex}>{x.sell_ex}"
            try:
                res = await (self.cross_exec.execute(x) if live else self._paper.execute_cross(x))
            except Exception as e:
                self.risk.halt(f"{name}: {e}" if isinstance(e, LegFailure) else f"{name} execution error {e!r}")
                self.journal(x.buy_ex + "/" + x.sell_ex, name, x.cost, 0.0, x.worst_bps, False)
                return
            self.settle(x.buy_ex + "/" + x.sell_ex, name, x.cost, res, x.worst_bps, (time.perf_counter() - t0) * 1000)
            if live:
                results = await asyncio.gather(bw.refresh_balance(), sw.refresh_balance(), return_exceptions=True)
                errors = [result for result in results if isinstance(result, Exception)]
                if errors:
                    self.risk.halt(
                        f"cross-venue balance refresh failed after order ({type(errors[0]).__name__})"
                    )
            await asyncio.sleep(self.cfg.cooldown_s)

    # ---- main -----------------------------------------------------------------
    def _push(self) -> None:
        for w in self.workers:
            if w.lat:
                self.stats.rtt[w.id] = round(w.lat.stats()["p50"], 1)
        if self.out is not None:
            self.out.put(("stats", self.stats.snapshot()))
        elif time.monotonic() - self._t_print > 10:
            self._t_print = time.monotonic()
            s = self.stats.snapshot()
            self.log("stats", f"scans={s['scans']} signals={s['signals']} trades={s['trades']} "
                              f"pnl={s['pnl']:+.4f} rejects={s['rejects']} rtt={s['rtt']}")

    async def run(self) -> None:
        self._loop, self._stop = asyncio.get_running_loop(), asyncio.Event()
        self.workers = [ExchangeWorker(x, self.cfg, self) for x in self.cfg.exchanges]
        tasks: list = []
        try:
            res = await asyncio.gather(*(w.prepare() for w in self.workers), return_exceptions=True)
            ok = []
            for w, r in zip(self.workers, res):
                if isinstance(r, Exception):
                    self.log("error", f"[{w.id}] disabled: {r}")
                    await w.close()
                else:
                    ok.append(w)
            if self.cfg.mode == "live" and len(ok) != len(self.workers):
                raise RuntimeError("live preflight failed for a selected venue; no live venue will be started")
            self.workers = ok
            if not ok:
                raise RuntimeError("no exchange passed preparation (see errors above)")
            if self.cross_active:
                self.cross_syms = self._pick_cross_symbols()
                if self.cfg.mode == "live":
                    self.cross_exec = CrossExecutor({w.id: w.ex for w in ok})
            for w in ok:
                w.start(self.cross_syms)
            tasks = [asyncio.create_task(w.run_loop(), name=f"loop:{w.id}") for w in ok]
            self.stats.status = f"RUNNING ({self.cfg.mode.upper()})"
            self.log("info", f"engine up | exchanges={[w.id for w in ok]} | cross-exchange symbols={len(self.cross_syms)}")
            while not self._stop.is_set() and not self.risk.halted:
                for t in tasks:
                    if t.done() and not t.cancelled() and t.exception():
                        self.risk.halt(f"worker crashed: {t.exception()!r}")
                for worker in ok:
                    health = worker.health_snapshot()
                    if health["verifiedStreamRequired"] and health["privateWebSocket"]["state"] != "LIVE":
                        self.risk.halt(f"[{worker.id}] authenticated private balance stream became stale or disconnected")
                        break
                    if self.cfg.mode == "live" and health["publicWebSocket"]["state"] != "LIVE":
                        self.risk.halt(f"[{worker.id}] public market-data WebSocket became stale or disconnected")
                        break
                    if self.cfg.mode == "live" and health["restLatencyMs"]["p95"] > self.cfg.pause_rtt_ms:
                        self.risk.halt(f"[{worker.id}] live latency exceeded the configured pause threshold")
                        break
                self._push()
                await asyncio.sleep(0.5)
            if self.risk.halted:
                self.log("halt", self.risk.reason)
        except Exception as e:
            self.log("error", f"engine error: {e!r}")
            self.stats.status = "ERROR"
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for w in self.workers:
                await w.close()
            if self.stats.status != "ERROR":
                self.stats.status = "HALTED" if self.risk.halted else "STOPPED"
            self._push()
            self.journal_store.close()
