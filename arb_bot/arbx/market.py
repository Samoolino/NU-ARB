"""Order-book streaming (ccxt.pro applies the exchange's incremental deltas to a local book; we snapshot the
top levels on every delta) and the latency guard."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass


def validate_book_snapshot(bids, asks, age_ms: float, max_age_ms: float) -> tuple[bool, str]:
    if not bids or not asks:
        return False, "missing_levels"
    if age_ms < 0 or age_ms > max_age_ms:
        return False, "stale_book"
    try:
        if any(float(p) <= 0 or float(q) <= 0 for p, q in list(bids) + list(asks)):
            return False, "invalid_level"
        if any(float(bids[i][0]) < float(bids[i + 1][0]) for i in range(len(bids) - 1)):
            return False, "unsorted_bids"
        if any(float(asks[i][0]) > float(asks[i + 1][0]) for i in range(len(asks) - 1)):
            return False, "unsorted_asks"
        if float(bids[0][0]) >= float(asks[0][0]):
            return False, "crossed_book"
    except (TypeError, ValueError, IndexError):
        return False, "invalid_level"
    return True, "ok"


@dataclass(slots=True)
class Book:
    bids: list
    asks: list
    recv: float   # time.monotonic() at receipt -> immune to exchange/local clock skew
    timestamp_exchange: int | None = None
    sequence: int | str | None = None
    source: str = "websocket"


def orderbook_limit(exchange_id: str, requested: int) -> int:
    limits = {"bybit": (1, 50, 200, 1000), "htx": (5, 20, 150, 400), "bitfinex": (25, 100)}
    allowed = limits.get(exchange_id)
    if allowed is None:
        return requested
    return next((limit for limit in allowed if limit >= requested), allowed[-1])


class MarketData:
    def __init__(self, ex, symbols, depth: int, log, max_age_ms: float = 250.0):
        self.ex, self.symbols, self.depth, self.log = ex, list(symbols), depth, log
        self.max_age_ms = max_age_ms
        self.books: dict[str, Book] = {}
        self.dirty: set[str] = set()
        self.event = asyncio.Event()

    def start(self) -> list:
        return ([asyncio.create_task(self._watch(s), name=f"ws:{s}") for s in self.symbols]
                + [asyncio.create_task(self._rest_fallback_loop(), name="rest-book-fallback")])

    async def _watch(self, sym: str) -> None:
        backoff = 1.0
        while True:
            try:
                limit = orderbook_limit(self.ex.id, self.depth)
                ob = await self.ex.watch_order_book(sym, limit)
                bids, asks = ob.get("bids", [])[:self.depth], ob.get("asks", [])[:self.depth]
                ok, reason = validate_book_snapshot(bids, asks, 0.0, self.max_age_ms)
                if not ok:
                    self.log("warn", f"{sym} rejected inconsistent WebSocket book: {reason}")
                    await asyncio.sleep(backoff)
                    continue
                self.books[sym] = Book(bids, asks, time.monotonic(), ob.get("timestamp"), ob.get("nonce"), "websocket")
                self.dirty.add(sym)
                self.event.set()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log("warn", f"{sym} stream error {e!r}; retry {backoff:.0f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


    async def _rest_fallback_loop(self) -> None:
        """Refresh at most three missing/stale books per sweep using REST."""
        cursor = 0
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            stale = [s for s in self.symbols if s not in self.books or
                     (now - self.books[s].recv) * 1000 > self.max_age_ms]
            if not stale:
                continue
            batch = stale[cursor:cursor + 3]
            if not batch:
                cursor = 0
                batch = stale[:3]
            for sym in batch:
                try:
                    ob = await self.ex.fetch_order_book(sym, orderbook_limit(self.ex.id, self.depth))
                    bids, asks = ob.get("bids", [])[:self.depth], ob.get("asks", [])[:self.depth]
                    ok, reason = validate_book_snapshot(bids, asks, 0.0, self.max_age_ms)
                    if not ok:
                        self.log("warn", f"{sym} REST fallback rejected: {reason}")
                        continue
                    self.books[sym] = Book(bids, asks, time.monotonic(), ob.get("timestamp"), ob.get("nonce"), "rest")
                    self.dirty.add(sym)
                    self.event.set()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.log("warn", f"{sym} REST fallback failed ({type(exc).__name__})")
            cursor = (cursor + len(batch)) % max(1, len(stale))


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

