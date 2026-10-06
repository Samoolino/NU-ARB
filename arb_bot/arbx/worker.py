"""One ExchangeWorker per exchange. Workers run concurrently on one event loop; an order in flight on one
exchange never blocks streaming or evaluation on another."""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress

from arbx.config import CCXT_ADAPTERS
from arbx.execute import LegFailure, LiveExecutor, PaperExecutor
from arbx.hybrid.execution import ExecutionCoordinator, ExecutionState
from arbx.graph import discover
from arbx.market import LatencyGuard, MarketData
from arbx.permissions import inspect_permissions
from arbx.strategy import evaluate_triangle
from arbx.util import STABLES


def spot_market_options(exchange_id: str) -> dict:
    options = {"defaultType": "spot"}
    if exchange_id == "htx":
        options["fetchMarkets"] = {"types": {"spot": True, "linear": False, "inverse": False}}
    elif exchange_id == "kucoin":
        options["fetchMarkets"] = {"types": ["spot"]}
    return options


def build_exchange(x, live: bool):
    import ccxt.pro as ccxtpro
    cls = getattr(ccxtpro, CCXT_ADAPTERS.get(x.id, x.id), None)
    if cls is None:
        raise RuntimeError(f"'{x.id}' is not supported by ccxt.pro (see ccxt.pro.exchanges)")
    params = {"enableRateLimit": True, "options": spot_market_options(x.venue_id or x.id)}
    if x.api_key and x.secret:
        secret = x.secret.encode("utf-8") if x.id == "binance" and x.auth_mode in ("rsa", "ed25519") else x.secret
        params.update(apiKey=x.api_key, secret=secret)
        if x.password:
            params["password"] = x.password
    return cls(params)


class ExchangeWorker:
    def __init__(self, x, cfg, hub):
        self.x, self.id, self.cfg, self.hub = x, x.id, cfg, hub
        self.live = cfg.mode == "live"
        self.private_stream_ready = False
        self.private_last_message: float | None = None
        self.private_stream_error: str | None = None
        self.balance_last_refresh: float | None = None
        self.ex = self.md = self.lat = self.executor = None
        self.by_symbol: dict = {}
        self.symbols: set = set()
        self.vol: dict = {}
        self.fees: dict = {}
        self.mlimits: dict = {}
        self.triangles: list = []
        self.free: dict = {}
        self.lock = asyncio.Lock()
        self.tasks: list = []

    # ---- helpers used by strategy / hub ---------------------------------
    def fee_of(self, sym: str) -> float:
        return self.fees[sym]

    def round_price(self, sym: str, p: float) -> float:
        try:
            return float(self.ex.price_to_precision(sym, p))
        except Exception:
            return p

    def free_of(self, asset: str) -> float:
        return self.free.get(asset, 0.0) if self.live else self.hub.equity

    def _index(self, symbols) -> None:
        disc = 1.0 - self.cfg.fee_discount_pct / 100.0
        for s in symbols:
            m = self.ex.markets[s]
            t = m.get("taker")
            self.fees[s] = (t if t is not None else self.x.default_taker_bps / 1e4) * disc
            lim = m.get("limits") or {}
            self.mlimits[s] = ((lim.get("amount") or {}).get("min"), (lim.get("cost") or {}).get("min"))

    # ---- lifecycle ------------------------------------------------------
    async def prepare(self) -> None:
        self.ex = build_exchange(self.x, self.live)
        await self.ex.load_markets()
        need_private_stream = self.live or self.x.require_private_stream
        if need_private_stream and self.x.api_key and self.x.secret and self.ex.has.get("watchBalance") is True:
            private_balance = await asyncio.wait_for(self.ex.watch_balance(), timeout=15)
            if not isinstance(private_balance, dict) or not all(key in private_balance for key in ("free", "used", "total")):
                raise RuntimeError("authenticated balance WebSocket did not return a unified balance snapshot")
            self.private_stream_ready = True
            self.private_last_message = time.monotonic()
            self.hub.log("info", f"[{self.id}] authenticated private balance WebSocket verified")
        elif need_private_stream:
            raise RuntimeError("authenticated private balance WebSocket is required but unavailable")
        if self.live:
            permissions = await inspect_permissions(self.x.venue_id or self.id, self.ex)
            if permissions.get("liveEligible") is not True:
                raise RuntimeError(
                    f"live key permissions are not verified for {self.x.venue_id or self.id}: "
                    f"{permissions.get('source', 'unknown permission probe')}"
                )
        self.lat = LatencyGuard(self.ex, self.cfg)
        st = await self.lat.preflight()
        self.hub.log("info", f"[{self.id}] REST RTT p50={st['p50']:.0f}ms p95={st['p95']:.0f}ms "
                             f"min={st['min']:.0f}ms clock-skew={st['skew']:+.0f}ms")
        if st["p50"] > self.cfg.max_rtt_ms:
            msg = (f"[{self.id}] median RTT {st['p50']:.0f}ms > {self.cfg.max_rtt_ms:.0f}ms limit: "
                   f"this host is too far from the exchange for latency-sensitive trading")
            if self.live:
                raise RuntimeError(msg + " - live disabled; move the bot next to the exchange (see README)")
            self.hub.log("warn", msg + " - paper results will be optimistic")
        try:
            tickers = await self.ex.fetch_tickers()
        except Exception as e:
            tickers = {}
            self.hub.log("warn", f"[{self.id}] tickers unavailable ({e!r}); ranking without volume")
        tris, self.symbols, self.vol = discover(self.ex.markets, tickers, self.cfg.start_assets,
                                                self.x.max_symbols, self.x.default_taker_bps)
        if not tris:
            raise RuntimeError("no triangular cycles found from the configured start assets")
        self.triangles = tris
        for t in tris:
            for l in t.legs:
                self.by_symbol.setdefault(l.symbol, []).append(t)
        self._index(self.symbols)
        if self.live:
            # Re-certify from the live execution path; a stale/manual
            # preflight must never be the only protection.
            from arbx.hybrid.engine import HybridEngine
            cert_symbol = os.getenv("BOT_PREFLIGHT_SYMBOL") or next(iter(self.symbols))
            hybrid = HybridEngine.create(self.cfg)
            venue_id = self.x.venue_id or self.id
            if venue_id not in hybrid.adapters:
                raise RuntimeError(f"live venue {venue_id} is not present in the hybrid certification catalog")
            evidence = await hybrid.validate_venue(
                venue_id, symbol=cert_symbol, notional_usd=self.cfg.trade_size_usd
            )
            if not evidence.live_eligible:
                raise RuntimeError(
                    f"live venue certification failed for {venue_id}: "
                    + ", ".join(evidence.reasons)
                )
            self.hub.log("info", f"[{self.id}] hybrid live certification passed for {cert_symbol}")
        self.executor = LiveExecutor(self.ex, self.cfg) if self.live else PaperExecutor(self.cfg)
        if self.live:
            missing = [name for name in ("createOrder", "createMarketOrder", "fetchOrder")
                       if self.ex.has.get(name) is not True]
            if missing:
                raise RuntimeError(f"live adapter lacks required execution capabilities: {', '.join(missing)}")
            feature_value = getattr(self.ex, "feature_value", None)
            symbol = next(iter(self.symbols))
            tif = feature_value(symbol, "createOrder", "timeInForce") if callable(feature_value) else None
            if not isinstance(tif, dict) or tif.get("IOC") is not True:
                raise RuntimeError("live adapter does not declare IOC limit support for the selected spot market")
            await self.refresh_balance()      # also warms the authenticated TLS connection
        self.hub.log("info", f"[{self.id}] {len(tris)} cycles over {len(self.symbols)} order books "
                             f"(cheapest vehicle: {min(t.fee_bps for t in tris):.1f} bps total taker fees)")

    def cross_candidates(self) -> dict:
        out = {}
        for s, v in self.vol.items():
            if v > 0 and self.ex.markets[s]["quote"] in self.cfg.start_assets:
                out[s] = v
        return out

    def start(self, extra) -> None:
        extra = [s for s in extra if s in self.ex.markets]
        self._index([s for s in extra if s not in self.fees])
        allsyms = sorted(self.symbols | set(extra))
        self.md = MarketData(self.ex, allsyms, self.cfg.depth, self.hub.log)
        self.tasks = self.md.start() + [asyncio.create_task(self.lat.loop(self.hub.log))]
        if self.live:
            self.tasks.extend((asyncio.create_task(self._balance_loop()),
                               asyncio.create_task(self._private_balance_loop())))
        elif self.private_stream_ready:
            self.tasks.append(asyncio.create_task(self._private_balance_loop()))

    def health_snapshot(self) -> dict:
        now = time.monotonic()
        latest = None
        if self.md and self.md.books:
            symbol, book = max(self.md.books.items(), key=lambda item: item[1].recv)
            latest = {"symbol": symbol, "bestBid": book.bids[0][0] if book.bids else None,
                      "bestAsk": book.asks[0][0] if book.asks else None,
                      "receivedAtAgeMs": round(max(0.0, now - book.recv) * 1000, 1),
                      "exchangeTimestamp": book.timestamp_exchange, "sequence": book.sequence,
                      "stale": (now - book.recv) * 1000 > self.cfg.max_book_age_ms}
        latency = self.lat.stats() if self.lat else {}
        needs_private = self.live or self.x.require_private_stream
        private_age = ((now - self.private_last_message) * 1000
                       if self.private_last_message is not None else None)
        private_fresh = not needs_private or (self.private_stream_ready and private_age is not None and private_age <= 120_000)
        return {"exchange": self.id,
                "state": "HEALTHY" if (latest is not None and not latest["stale"] and private_fresh and self.lat and self.lat.ok()) else "DEGRADED",
                "publicWebSocket": {"state": "LIVE" if latest is not None and not latest["stale"] else "STALE",
                                    "latestBook": latest},
                "privateWebSocket": {"state": "NOT_REQUIRED" if not needs_private else "LIVE" if private_fresh else "STALE",
                                     "lastMessageAgeMs": round(private_age, 1) if private_age is not None else None,
                                     "error": self.private_stream_error},
                "restLatencyMs": {"p50": round(latency.get("p50", 0), 1),
                                  "p95": round(latency.get("p95", 0), 1),
                                  "clockDriftMs": round(latency.get("skew", 0), 1)},
                "verifiedStreamRequired": needs_private}

    async def close(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.ex is not None:
            with suppress(Exception):
                await self.ex.close()

    async def refresh_balance(self) -> None:
        balance = await self.ex.fetch_balance()
        if not isinstance(balance, dict) or not isinstance(balance.get("free"), dict):
            raise RuntimeError("authenticated REST balance refresh returned no free-balance map")
        self.free = {k: float(v) for k, v in balance["free"].items() if v}
        self.balance_last_refresh = time.monotonic()

    async def _balance_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                await self.refresh_balance()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.hub.risk.halt(f"[{self.id}] authenticated balance refresh failed ({type(exc).__name__})")
                raise

    async def _private_balance_loop(self) -> None:
        while True:
            try:
                balance = await self.ex.watch_balance()
                self.free = {k: float(v) for k, v in (balance.get("free") or {}).items() if v}
                self.private_last_message = time.monotonic()
                self.balance_last_refresh = self.private_last_message
            except Exception as exc:
                self.private_stream_error = type(exc).__name__
                self.private_stream_ready = False
                self.hub.log("error", f"[{self.id}] private balance stream failed ({type(exc).__name__})")
                raise

    # ---- hot loop ---------------------------------------------------------
    async def run_loop(self) -> None:
        md, cfg, hub = self.md, self.cfg, self.hub
        while True:
            await md.event.wait()
            md.event.clear()
            dirty, md.dirty = md.dirty, set()
            now, size, best, seen = time.monotonic(), hub.trade_size(), None, set()
            for sym in dirty:                                   # only cycles touching a changed book
                for tri in self.by_symbol.get(sym, ()):
                    if id(tri) in seen:
                        continue
                    seen.add(id(tri))
                    hub.stats.scans += 1
                    o = evaluate_triangle(tri, md.books, size, self.fee_of, cfg.min_net_bps,
                                          cfg.limit_tol_bps, self.round_price, now)
                    if o and (best is None or o.worst_bps > best.worst_bps):
                        best = o
            if best:
                await self._fire(best)
            if hub.cross_syms and (dirty & hub.cross_syms):
                await hub.scan_cross(self, dirty)

    async def _fire(self, o) -> None:
        hub = self.hub
        hub.stats.signals += 1
        if self.lock.locked():
            return
        async with self.lock:
            if self.live:
                try:
                    await self.refresh_balance()
                except Exception as exc:
                    hub.risk.halt(f"[{self.id}] could not refresh balance before order ({type(exc).__name__})")
                    return
                tri = next((item for item in self.triangles if item.name == o.name), None)
                if tri is None:
                    hub.risk.halt(f"[{self.id}] selected cycle disappeared before execution")
                    return
                o = evaluate_triangle(tri, self.md.books, hub.trade_size(), self.fee_of,
                                      self.cfg.min_net_bps, self.cfg.limit_tol_bps,
                                      self.round_price, time.monotonic())
                if o is None:
                    hub.stats.rejects["opportunity_disappeared"] += 1
                    return
            d = hub.gate.check_tri(o, self.mlimits, self.lat.ok(), self.free_of(o.start_asset))
            hub.record_opportunity(
                strategy="triangular", exchange_a=self.id, exchange_b=None,
                symbol=",".join(leg.symbol for leg in o.legs), requested_usd=o.start,
                expected_net_usd=o.expected_final - o.start,
                worst_case_net_usd=o.worst_final - o.start,
                expected_net_bps=o.net_bps, worst_case_net_bps=o.worst_bps,
                book_age_ms=o.age_ms, approved=d.ok, rejection_reason=None if d.ok else d.reason,
                evidence={
                    "limits": list(o.limits),
                    "legs": [{"symbol": leg.symbol, "side": leg.side} for leg in o.legs],
                    "restLatency": self.lat.stats(),
                    "bookSequences": {leg.symbol: self.md.books[leg.symbol].sequence for leg in o.legs},
                    "bookDepthLevels": {leg.symbol: {
                        "bids": len(self.md.books[leg.symbol].bids),
                        "asks": len(self.md.books[leg.symbol].asks),
                    } for leg in o.legs},
                },
            )
            if not d.ok:
                hub.stats.rejects[d.reason] += 1
                return
            t0 = time.perf_counter()
            coordinator = ExecutionCoordinator()
            if not coordinator.gate(expected_net_usd=o.expected_final - o.start, worst_case_net_usd=o.worst_final - o.start, min_profit_usd=max(0.0, getattr(cfg, 'min_profit_usd', 0.0)), min_worst_profit_usd=max(0.0, getattr(cfg, 'min_worst_profit_usd', 0.0))):
                hub.stats.rejects['execution_profitability_gate'] += 1
                return
            coordinator.on_submission()
            try:
                res = await self.executor.execute_tri(o)
            except Exception as e:
                coordinator.fail(str(e))
                coordinator.require_reconciliation()
                hub.risk.halt(f"[{self.id}] {o.name}: {e}" if isinstance(e, LegFailure) else f"[{self.id}] execution error {e!r}")
                hub.journal(self.id, o.name, o.start, 0.0, o.worst_bps, False)
                return
            if not res.ok:
                coordinator.fail('executor returned unsuccessful result')
                hub.stats.rejects['execution_unsuccessful'] += 1
                return
            coordinator.on_fill(o.start, o.start + res.pnl)
            coordinator.require_reconciliation()
            coordinator.on_realized_pnl(res.pnl)
            hub.settle(self.id, o.name, o.start, res, o.worst_bps, (time.perf_counter() - t0) * 1000.0)
            if coordinator.halted:
                hub.risk.halt(f"[{self.id}] realized loss halted new engagements")
            elif coordinator.state != ExecutionState.HALTED:
                coordinator.complete()
            if self.live:
                asyncio.create_task(self.refresh_balance())
            await asyncio.sleep(self.cfg.cooldown_s)
