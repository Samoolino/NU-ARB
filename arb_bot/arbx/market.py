"""Order-book streaming (ccxt.pro applies the exchange's incremental deltas to a local book; we snapshot the
top levels on every delta) and the latency guard."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass


@dataclass(slots=True)
class Book:
    bids: list
    asks: list
    recv: float   # time.monotonic() at receipt -> immune to exchange/local clock skew
    timestamp_exchange: int | None = None
    sequence: int | str | None = None
    volatility_bps_s: float = 0.0


def orderbook_limit(exchange_id: str, requested: int) -> int:
    limits = {"bybit": (1, 50, 200, 1000), "htx": (5, 20, 150, 400), "bitfinex": (25, 100)}
    allowed = limits.get(exchange_id)
    if allowed is None:
        return requested
    return next((limit for limit in allowed if limit >= requested), allowed[-1])


class MarketData:
    def __init__(self, ex, symbols, depth: int, log):
        self.ex, self.symbols, self.depth, self.log = ex, list(symbols), depth, log
        self.books: dict[str, Book] = {}
        self._mid: dict[str, tuple[float, float]] = {}
        self.dirty: set[str] = set()
        self.event = asyncio.Event()

    def start(self) -> list:
        return [asyncio.create_task(self._watch(s), name=f"ws:{s}") for s in self.symbols]

    async def _watch(self, sym: str) -> None:
        backoff = 1.0
        while True:
            try:
                limit = orderbook_limit(self.ex.id, self.depth)
                ob = await self.ex.watch_order_book(sym, limit)
                recv = time.monotonic()
                bids, asks = ob["bids"][: self.depth], ob["asks"][: self.depth]
                volatility_bps_s = 0.0
                if bids and asks:
                    mid = (float(bids[0][0]) + float(asks[0][0])) / 2.0
                    previous = self._mid.get(sym)
                    if previous is not None and previous[0] > 0 and recv > previous[1]:
                        move_bps = abs(mid / previous[0] - 1.0) * 1e4
                        volatility_bps_s = min(100000.0, move_bps / (recv - previous[1]))
                    self._mid[sym] = (mid, recv)
                previous_book = self.books.get(sym)
                if previous_book is not None:
                    volatility_bps_s = 0.7 * previous_book.volatility_bps_s + 0.3 * volatility_bps_s
                self.books[sym] = Book(bids, asks, recv, ob.get("timestamp"), ob.get("nonce"), volatility_bps_s)
                self.dirty.add(sym)
                self.event.set()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log("warn", f"{sym} stream error {e!r}; retry {backoff:.0f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


class LatencyGuard:
    """Measures REST round-trip to the exchange. Live mode refuses to start if the host is too far away and
    trading pauses automatically while the rolling p95 is degraded."""

    def __init__(self, ex, cfg):
        self.ex, self.cfg = ex, cfg
        self.samples: deque = deque(maxlen=30)
        self.skew_ms = 0.0

    async def sample(self) -> float:
        t0, p0 = time.time(), time.perf_counter()
        srv = await self.ex.fetch_time()
        rtt = (time.perf_counter() - p0) * 1000.0
        if srv:
            self.skew_ms = srv - (t0 * 1000.0 + rtt / 2.0)
        return rtt

    async def preflight(self, n: int = 9) -> dict:
        await self.sample()                       # discard: includes TCP+TLS handshake
        for _ in range(n):
            self.samples.append(await self.sample())
            await asyncio.sleep(0.05)
        return self.stats()

    def stats(self) -> dict:
        s = sorted(self.samples) or [0.0]
        return {"p50": s[len(s) // 2], "p95": s[min(len(s) - 1, int(len(s) * 0.95))],
                "min": s[0], "skew": self.skew_ms}

    def ok(self) -> bool:
        return not self.samples or self.stats()["p95"] <= self.cfg.pause_rtt_ms

    async def loop(self, log) -> None:
        while True:
            await asyncio.sleep(15)
            try:
                self.samples.append(await self.sample())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log("warn", f"latency probe failed: {e!r}")

