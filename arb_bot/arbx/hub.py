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
from arbx.capital import live_engagement_after_verified_pnl
from arbx.ranking import rank_target_progress
from arbx.strategy import cross_candidate_sizes, evaluate_cross
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
    venue_health: dict = field(default_factory=dict)
    market_universe: dict = field(default_factory=dict)

    def snapshot(self) -> dict:
        return {"status": self.status, "session_id": self.session_id,
                "scans": self.scans, "signals": self.signals, "trades": self.trades,
                "failed": self.failed, "pnl": self.pnl, "equity": self.equity,
                "target_profit_usd": self.target_profit_usd, "target_progress_pct": self.target_progress_pct,
                "rejects": dict(self.rejects.most_common(4)), "rtt": dict(self.rtt),
                "venue_health": dict(self.venue_health), "market_universe": dict(self.market_universe)}


class Hub:
    def __init__(self, cfg: Config, out: "queue.Queue | None" = None):
        self.cfg, self.out = cfg, out
        self.session_id = os.getenv("BOT_SESSION_ID") or uuid.uuid4().hex
        self.journal_store = TradeJournal(cfg.journal_path.with_suffix(".sqlite3"))
        self.risk = RiskManager(cfg, on_halt=lambda reason: self.journal_store.record_session_halt(self.session_id, reason))
        durable_halt = self.journal_store.session_halt(self.session_id)
        if durable_halt:
            self.risk.halted = True
            self.risk.reason = durable_halt["reason"]
        self.risk.pnl = self.journal_store.realized(self.session_id)
        self.unresolved_live_executions = self.journal_store.open_executions(mode="live")
        if self.cfg.mode == "live" and self.unresolved_live_executions:
            self.risk.halt("unresolved durable live execution state requires reconciliation before new orders")
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
        self._cross_scan_lock = asyncio.Lock()
        self._cross_scan_last = 0.0
        self._opportunity_last_written: dict[str, float] = {}
        self._paper = PaperExecutor(cfg)
        self._loop = self._stop = None
        self._t_print = 0.0

    # ---- utilities ------------------------------------------------------
    @property
    def equity(self) -> float:
        return self.cfg.start_capital_usd + self.risk.pnl

    @staticmethod
    def _latency_p95(lat) -> float:
        stats = getattr(lat, "stats", None)
        if callable(stats):
            return float(stats().get("p95", 0.0) or 0.0)
        return float(getattr(lat, "p95", 0.0) or 0.0)

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
        """Post-trade verification compares realized PnL with the modeled pre-trade floor."""
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
                self.risk.halt(f"MODELED FLOOR BREACHED on {name}: realized {realized:.2f}bps < modeled floor "
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
                           rejection_reason: str | None, evidence: dict,
                           decision: str | None = None, force: bool = False) -> None:
        key = f"{strategy}:{exchange_a}:{exchange_b or ''}:{symbol}"
        now = time.monotonic()
        if not force and now - self._opportunity_last_written.get(key, 0.0) < 1.0:
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
            "decision": decision or ("APPROVED_BY_GATE" if approved else "REJECTED_BY_GATE"),
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

    def _cross_budget_usd(self, bw, sw, symbol: str, buy_book) -> tuple[float, float, float]:
        if self.cfg.mode != "live":
            size = self.trade_size()
            return size, math.inf, math.inf
        if not buy_book.asks:
            return 0.0, 0.0, 0.0
        market = bw.ex.markets[symbol]
        quote, base = market["quote"], market["base"]
        free_quote = float(bw.free.get(quote, 0.0))
        free_base = float(sw.free.get(base, 0.0))
        ask = float(buy_book.asks[0][0])
        fee = max(0.0, bw.fee_of(symbol))
        quote_budget = free_quote / (1.0 + fee + 0.002)
        size = min(self.cfg.trade_size_usd, quote_budget, free_base * ask)
        return max(0.0, size), free_quote, free_base

    def _best_cross_for_direction(self, symbol, bw, sw, buy_book, sell_book, now):
        size, free_quote, free_base = self._cross_budget_usd(bw, sw, symbol, buy_book)
        best = None
        for candidate_size in cross_candidate_sizes(buy_book, sell_book, size):
            opportunity = evaluate_cross(symbol, bw, sw, buy_book, sell_book,
                                         candidate_size, self.cfg, now)
            if opportunity is None:
                continue
            predicted_ms = max(self._latency_p95(bw.lat), self._latency_p95(sw.lat)) * 2.0 + 20.0
            speed_margin_bps = (
                opportunity.volatility_bps_s * predicted_ms / 1000.0
                + self.cfg.speed_safety_buffer_bps
            )
            decision = self.gate.check_cross(
                opportunity, bw.mlimits, sw.mlimits, bw.lat.ok() and sw.lat.ok(),
                free_quote, free_base, speed_margin_bps=speed_margin_bps,
            )
            utilization = opportunity.cost / free_quote if free_quote > 0 and math.isfinite(free_quote) else 0.0
            key = (decision.ok, opportunity.worst_usd, opportunity.expected_usd,
                   utilization, opportunity.worst_bps, opportunity.cost)
            if best is None or key > best[0]:
                best = (key, opportunity, decision, free_quote, free_base, utilization)
        return best

    async def scan_cross(self, w, dirty) -> None:
        if self._cross_scan_lock.locked() or self.risk.halted:
            return
        now = time.monotonic()
        if now - self._cross_scan_last < 0.1:
            return
        self._cross_scan_last = now
        async with self._cross_scan_lock:
            ranked = []
            target_remaining = None
            if self.cfg.target_profit_usd is not None:
                target_remaining = max(0.0, self.cfg.target_profit_usd - self.risk.pnl)
            workers = self.workers
            for symbol in self.cross_syms:
                available = [(worker, worker.md.books.get(symbol)) for worker in workers]
                available = [(worker, book) for worker, book in available if book is not None]
                for index, (buy_worker, buy_book) in enumerate(available):
                    for sell_worker, sell_book in available[index + 1:]:
                        if (buy_worker.lock.locked() or sell_worker.lock.locked()) and self.cfg.mode == "live":
                            continue
                        for bw, sw, bb, sb in (
                            (buy_worker, sell_worker, buy_book, sell_book),
                            (sell_worker, buy_worker, sell_book, buy_book),
                        ):
                            candidate = self._best_cross_for_direction(symbol, bw, sw, bb, sb, now)
                            if candidate:
                                _, opportunity, decision, free_quote, free_base, utilization = candidate
                                target_rank = rank_target_progress(
                                    expected_net_usd=opportunity.expected_usd,
                                    worst_case_net_usd=opportunity.worst_usd,
                                    worst_case_net_bps=opportunity.worst_bps,
                                    age_ms=opportunity.age_ms,
                                    max_book_age_ms=self.cfg.max_book_age_ms,
                                    capital_utilization=utilization,
                                    target_remaining_usd=target_remaining,
                                    volatility_bps_s=opportunity.volatility_bps_s,
                                    predicted_completion_ms=max(bw.lat.stats()["p95"], sw.lat.stats()["p95"]) * 2.0 + 20.0,
                                    speed_safety_buffer_bps=self.cfg.speed_safety_buffer_bps,
                                )
                                ranked.append((decision.ok, target_rank.score, opportunity.worst_usd,
                                               opportunity.expected_usd, utilization, opportunity.worst_bps,
                                               opportunity.cost, target_rank, opportunity, bw, sw, decision,
                                               free_quote, free_base, utilization))
            ranked.sort(key=lambda item: item[:7], reverse=True)
            if not ranked:
                return

            selected = ranked[0]
            for rank, candidate in enumerate(ranked[1:21], start=2):
                _, target_score, _, _, _, _, _, target_rank, opportunity, bw, sw, decision, free_quote, free_base, utilization = candidate
                reason = decision.reason if not decision.ok else "lower_priority_than_selected"
                self.record_opportunity(
                    strategy="cross_exchange", exchange_a=opportunity.buy_ex, exchange_b=opportunity.sell_ex,
                    symbol=opportunity.symbol, requested_usd=opportunity.cost,
                    expected_net_usd=opportunity.expected_usd, worst_case_net_usd=opportunity.worst_usd,
                    expected_net_bps=opportunity.net_bps, worst_case_net_bps=opportunity.worst_bps,
                    book_age_ms=opportunity.age_ms, approved=False, rejection_reason=reason,
                    decision="REJECTED_BY_GATE" if not decision.ok else "RANKED_NOT_SELECTED",
                    evidence={"priorityRank": rank, "targetProgressScore": target_score,
                              "executionConfidence": target_rank.execution_confidence,
                              "expectedTargetProgress": target_rank.expected_target_progress,
                              "selectedTargetProgressScore": selected[1],
                              "selectedFloorUsd": selected[2],
                              "availableQuoteUsd": free_quote if math.isfinite(free_quote) else None,
                              "availableBase": free_base if math.isfinite(free_base) else None,
                              "capitalUtilization": utilization,
                              "volatilityBpsS": target_rank.volatility_bps_s,
                              "speedMarginBps": target_rank.speed_margin_bps},
                )
            _, target_score, _, _, _, _, _, target_rank, opportunity, bw, sw, _, free_quote, free_base, utilization = selected
            self.record_opportunity(
                strategy="cross_exchange", exchange_a=opportunity.buy_ex, exchange_b=opportunity.sell_ex,
                symbol=opportunity.symbol, requested_usd=opportunity.cost,
                expected_net_usd=opportunity.expected_usd, worst_case_net_usd=opportunity.worst_usd,
                expected_net_bps=opportunity.net_bps, worst_case_net_bps=opportunity.worst_bps,
                book_age_ms=opportunity.age_ms, approved=True, rejection_reason=None,
                decision="SELECTED_TARGET_PROGRESS",
                evidence={"priorityRank": 1, "targetProgressScore": target_score,
                          "executionConfidence": target_rank.execution_confidence,
                          "expectedTargetProgress": target_rank.expected_target_progress,
                          "targetRemainingUsd": target_remaining,
                          "capitalUtilization": utilization,
                          "volatilityBpsS": target_rank.volatility_bps_s,
                          "speedMarginBps": target_rank.speed_margin_bps},
                force=True,
            )
            await self._fire_cross(opportunity, bw, sw, priority_rank=1,
                                   compared_count=len(ranked), free_quote=free_quote,
                                   free_base=free_base, utilization=utilization)

    async def _fire_cross(self, x, bw, sw, *, priority_rank: int = 1, compared_count: int = 1,
                          free_quote: float = 0.0, free_base: float = 0.0,
                          utilization: float = 0.0) -> None:
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
                for worker in (bw, sw):
                    baseline = self.journal_store.capital_baseline(self.session_id, worker.id)
                    if baseline is None:
                        self.journal_store.record_capital_baseline(self.session_id, worker.id, worker.free)
                    elif not self.journal_store.reconcile_capital(
                        session_id=self.session_id,
                        execution_id=f"admission:{self.session_id}:{worker.id}:{time.time_ns()}",
                        exchange_id=worker.id,
                        before=baseline,
                        after=worker.free,
                        expected_deltas={},
                    ):
                        self.risk.halt(f"[{worker.id}] unexplained positive capital delta before live admission")
                        return
            buy_book = bw.md.books.get(x.symbol)
            sell_book = sw.md.books.get(x.symbol)
            if buy_book is None or sell_book is None:
                self.stats.rejects["missing_order_book"] += 1
                return
            candidate = self._best_cross_for_direction(x.symbol, bw, sw, buy_book, sell_book,
                                                        time.monotonic())
            if candidate is None:
                self.stats.rejects["opportunity_disappeared"] += 1
                return
            _, x, d, free_quote, free_base, utilization = candidate
            utilization = x.cost / free_quote if free_quote > 0 and math.isfinite(free_quote) else 0.0
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
                    "priorityRank": priority_rank, "comparedCandidateCount": compared_count,
                    "availableQuoteUsd": free_quote if math.isfinite(free_quote) else None,
                    "availableBase": free_base if math.isfinite(free_base) else None,
                    "capitalUtilization": utilization,
                    "volatilityBpsS": x.volatility_bps_s,
                    "predictedCompletionMs": max(bw.lat.stats()["p95"], sw.lat.stats()["p95"]) * 2.0 + 20.0,
                    "speedMarginBps": x.volatility_bps_s * (max(bw.lat.stats()["p95"], sw.lat.stats()["p95"]) * 2.0 + 20.0) / 1000.0 + self.cfg.speed_safety_buffer_bps,
                },
                force=True,
            )
            if not d.ok:
                self.stats.rejects[d.reason] += 1
                return
            reservation = None
            execution_id = None
            if live:
                reservation = self.journal_store.reservations.acquire(
                    session_id=self.session_id,
                    opportunity_id=f"cross:{x.buy_ex}:{x.sell_ex}:{x.symbol}:{buy_book.sequence}:{sell_book.sequence}",
                    resources=[
                        (f"inventory:{bw.id}:{x.quote_ccy}", max(0.000001, x.cost * (1.0 + max(0.0, bw.fee_of(x.symbol)))), max(0.000001, free_quote)),
                        (f"inventory:{sw.id}:{x.symbol.split("/")[0]}", max(0.000001, x.base), max(0.000001, free_base)),
                        (f"execution-slot:{self.session_id}", 1.0, 1.0),
                        (f"rate-slot:{bw.id}", 1.0, float(max(1, self.cfg.max_trades_per_min))),
                        (f"rate-slot:{sw.id}", 1.0, float(max(1, self.cfg.max_trades_per_min))),
                    ],
                    ttl_s=self.cfg.reservation_ttl_s,
                )
                if reservation is None:
                    self.stats.rejects["reservation_unavailable"] += 1
                    self.log("warn", f"[{name if "name" in locals() else x.symbol}] live admission rejected: durable reservation unavailable")
                    return
            t0, name = time.perf_counter(), f"X {x.symbol} {x.buy_ex}>{x.sell_ex}"
            if live:
                execution_id = uuid.uuid4().hex
                self.journal_store.create_execution(
                    execution_id=execution_id,
                    session_id=self.session_id,
                    mode=self.cfg.mode,
                    strategy="cross_exchange",
                    opportunity_id=f"cross:{x.buy_ex}:{x.sell_ex}:{x.symbol}:{buy_book.sequence}:{sell_book.sequence}",
                    legs=[
                        {"leg_index": 0, "exchange_id": x.buy_ex, "symbol": x.symbol, "side": "buy", "requested_amount": x.base},
                        {"leg_index": 1, "exchange_id": x.sell_ex, "symbol": x.symbol, "side": "sell", "requested_amount": x.base},
                    ],
                )
            try:
                res = await (self.cross_exec.execute(x, execution_id=execution_id) if live else self._paper.execute_cross(x))
            except Exception as e:
                self.risk.halt(f"{name}: {e}" if isinstance(e, LegFailure) else f"{name} execution error {e!r}")
                self.journal(x.buy_ex + "/" + x.sell_ex, name, x.cost, 0.0, x.worst_bps, False)
                return
            finally:
                if reservation is not None:
                    self.journal_store.reservations.release(reservation.reservation_id)
            completion_ms = float(getattr(res, "execution_ms", 0.0) or 0.0) or (time.perf_counter() - t0) * 1000.0
            actual_speed_margin_bps = x.volatility_bps_s * completion_ms / 1000.0 + self.cfg.speed_safety_buffer_bps
            self.log("metric", f"[{name}] completion={completion_ms:.1f}ms volatility={x.volatility_bps_s:.2f}bps/s speed_margin={actual_speed_margin_bps:.2f}bps")
            if live and actual_speed_margin_bps + self.cfg.min_worst_bps > x.worst_bps:
                self.risk.halt(
                    f"{name}: completion speed consumed modeled volatility margin "
                    f"({actual_speed_margin_bps:.2f}bps; worst floor {x.worst_bps:.2f}bps)"
                )
            if not live:
                self.settle(x.buy_ex + "/" + x.sell_ex, name, x.cost, res, x.worst_bps, completion_ms)
            if live:
                results = await asyncio.gather(bw.refresh_balance(), sw.refresh_balance(), return_exceptions=True)
                errors = [result for result in results if isinstance(result, Exception)]
                if errors:
                    self.risk.halt(
                        f"cross-venue balance refresh failed after order ({type(errors[0]).__name__})"
                    )
                else:
                    self.journal_store.record_settlement(execution_id, bw.id, bw.free)
                    self.journal_store.record_settlement(execution_id, sw.id, sw.free)
                    if not self.journal_store.settlement_complete(execution_id, [bw.id, sw.id]):
                        self.risk.halt("post-trade settlement evidence incomplete across selected venues")
                        return
                    clean_a = self.journal_store.reconcile_capital(
                        session_id=self.session_id,
                        execution_id=execution_id,
                        exchange_id=bw.id,
                        before=self.journal_store.capital_baseline(self.session_id, bw.id) or bw.free,
                        after=bw.free,
                        expected_deltas=self.journal_store.execution_expected_deltas(execution_id, bw.id),
                    )
                    clean_b = self.journal_store.reconcile_capital(
                        session_id=self.session_id,
                        execution_id=execution_id,
                        exchange_id=sw.id,
                        before=self.journal_store.capital_baseline(self.session_id, sw.id) or sw.free,
                        after=sw.free,
                        expected_deltas=self.journal_store.execution_expected_deltas(execution_id, sw.id),
                    )
                    if not (clean_a and clean_b):
                        self.risk.halt("capital reconciliation exception: unexplained positive balance delta")
                        return
                    if res.ok:
                        self.journal_store.transition_execution(execution_id, "VERIFIED")
                        gross = float(res.pnl or 0.0) + float(getattr(res, "fees", 0.0) or 0.0)
                        fees = float(getattr(res, "fees", 0.0) or 0.0)
                        self.journal_store.record_verified_result(
                            execution_id=execution_id,
                            session_id=self.session_id,
                            mode="live",
                            gross_pnl=gross,
                            fees=fees,
                            net_pnl=float(res.pnl or 0.0),
                        )
                    else:
                        self.journal_store.transition_execution(execution_id, "RELEASED")
                    self.settle(x.buy_ex + "/" + x.sell_ex, name, x.cost, res, x.worst_bps, (time.perf_counter() - t0) * 1000)
                    if live_engagement_after_verified_pnl(float(res.pnl or 0.0)) == "HALT_LOSS":
                        self.risk.halt(
                            f"live engagement halted after verified loss: net PnL {float(res.pnl or 0.0):+.4f} USD"
                        )
                        return
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
                    self.cross_exec = CrossExecutor({w.id: w.ex for w in ok}, execution_store=self.journal_store, session_id=self.session_id)
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
