"""One ExchangeWorker per exchange. Workers run concurrently on one event loop; an order in flight on one
exchange never blocks streaming or evaluation on another."""
from __future__ import annotations

import asyncio
import time
from contextlib import suppress

from arbx.execute import LegFailure, LiveExecutor, PaperExecutor
from arbx.graph import discover
from arbx.market import LatencyGuard, MarketData
from arbx.strategy import evaluate_triangle
from arbx.util import STABLES


def build_exchange(x, live: bool):
    import ccxt.pro as ccxtpro
    cls = getattr(ccxtpro, x.id, None)
    if cls is None:
        raise RuntimeError(f"'{x.id}' is not supported by ccxt.pro (see ccxt.pro.exchanges)")
    params = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
    if live:
        params.update(apiKey=x.api_key, secret=x.secret)
        if x.password:
            params["password"] = x.password
    return cls(params)


class ExchangeWorker:
    def __init__(self, x, cfg, hub):
        self.x, self.id, self.cfg, self.hub = x, x.id, cfg, hub
        self.live = cfg.mode == "live"
        self.ex = self.md = self.lat = self.executor = None
        self.by_symbol: dict = {}
        self.symbols: set = set()
        self.vol: dict = {}
        self.fees: dict = {}
        self.mlimits: dict = {}
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
        for t in tris:
            for l in t.legs:
                self.by_symbol.setdefault(l.symbol, []).append(t)
        self._index(self.symbols)
        self.executor = LiveExecutor(self.ex, self.cfg) if self.live else PaperExecutor(self.cfg)
        if self.live:
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
            self.tasks.append(asyncio.create_task(self._balance_loop()))

    async def close(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.ex is not None:
            with suppress(Exception):
                await self.ex.close()

    async def refresh_balance(self) -> None:
        with suppress(Exception):
            self.free = {k: float(v) for k, v in ((await self.ex.fetch_balance()).get("free") or {}).items() if v}

    async def _balance_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            await self.refresh_balance()

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
        d = hub.gate.check_tri(o, self.mlimits, self.lat.ok(), self.free_of(o.start_asset))
        if not d.ok:
            hub.stats.rejects[d.reason] += 1
            return
        async with self.lock:
            t0 = time.perf_counter()
            try:
                res = await self.executor.execute_tri(o)
            except Exception as e:
                hub.risk.halt(f"[{self.id}] {o.name}: {e}" if isinstance(e, LegFailure)
                              else f"[{self.id}] execution error {e!r}")
                hub.journal(self.id, o.name, o.start, 0.0, o.worst_bps, False)
                return
            hub.settle(self.id, o.name, o.start, res, o.worst_bps, (time.perf_counter() - t0) * 1000.0)
            if self.live:
                asyncio.create_task(self.refresh_balance())
            await asyncio.sleep(self.cfg.cooldown_s)
