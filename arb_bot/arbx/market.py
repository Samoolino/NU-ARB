"""Order-book streaming (ccxt.pro applies the exchange's incremental deltas to a local book; we snapshot the
top levels on every delta) and the latency guard."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass


MAX_STREAM_EXCHANGE_AGE_MS = 5_000
MAX_STREAM_FUTURE_SKEW_MS = 5_000
MAX_CROSS_STREAM_TIMESTAMP_SKEW_MS = 5_000
STREAM_IDLE_TIMEOUT_S = 15.0
RECONNECT_THROTTLE_S = 2.0


def cross_timestamp_consistent(buy_timestamp: int | float | None, sell_timestamp: int | float | None, *, max_skew_ms: float = MAX_CROSS_STREAM_TIMESTAMP_SKEW_MS) -> bool:
    if buy_timestamp is None or sell_timestamp is None:
        return False
    try:
        buy, sell = float(buy_timestamp), float(sell_timestamp)
    except (TypeError, ValueError):
        return False
    import math
    return math.isfinite(buy) and math.isfinite(sell) and buy > 0 and sell > 0 and abs(buy - sell) <= max_skew_ms


def validate_stream_order_book(ob: dict, *, now_ms: int | None = None) -> str | None:
    """Validate one normalized CCXT Pro snapshot before it can enter shared market state."""
    import math
    import time as _time

    now_ms = int(_time.time() * 1000) if now_ms is None else int(now_ms)
    timestamp = ob.get("timestamp")
    if timestamp is None or isinstance(timestamp, bool):
        return "missing_exchange_timestamp"
    try:
        timestamp = float(timestamp)
    except (TypeError, ValueError):
        return "invalid_exchange_timestamp"
    if not math.isfinite(timestamp) or timestamp <= 0:
        return "invalid_exchange_timestamp"
    if timestamp - now_ms > MAX_STREAM_FUTURE_SKEW_MS:
        return "future_exchange_timestamp"
    if now_ms - timestamp > MAX_STREAM_EXCHANGE_AGE_MS:
        return "stale_exchange_timestamp"
    bids, asks = ob.get("bids"), ob.get("asks")
    if not isinstance(bids, list) or not bids or not isinstance(asks, list) or not asks:
        return "empty_order_book"
    previous = None
    for level in bids:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            return "invalid_level"
        try:
            price, amount = float(level[0]), float(level[1])
        except (TypeError, ValueError):
            return "invalid_level"
        if not math.isfinite(price) or not math.isfinite(amount) or price <= 0 or amount <= 0:
            return "invalid_level"
        if previous is not None and price > previous:
            return "unsorted_bids"
        previous = price
    previous = None
    for level in asks:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            return "invalid_level"
        try:
            price, amount = float(level[0]), float(level[1])
        except (TypeError, ValueError):
            return "invalid_level"
        if not math.isfinite(price) or not math.isfinite(amount) or price <= 0 or amount <= 0:
            return "invalid_level"
        if previous is not None and price < previous:
            return "unsorted_asks"
        previous = price
    if float(bids[0][0]) >= float(asks[0][0]):
        return "crossed_order_book"
    return None


@dataclass(slots=True)
class Book:
    bids: list
    asks: list
    recv: float   # time.monotonic() at receipt -> immune to exchange/local clock skew
    timestamp_exchange: int | None = None
    sequence: int | str | None = None


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
        self.dirty: set[str] = set()
        self.event = asyncio.Event()
        # State is latest-value-per-symbol; dirty is a bounded set, never an unbounded update queue.
        self.max_buffered_symbols = len(self.symbols)
        self._reconnect_lock = asyncio.Lock()
        self._last_reconnect_at = 0.0

    async def _recover_connection(self) -> None:
        async with self._reconnect_lock:
            now = time.monotonic()
            if now - self._last_reconnect_at < RECONNECT_THROTTLE_S:
                return
            self._last_reconnect_at = now
            self.log("warn", "reinitializing exchange WebSocket transport after stream timeout/error")
            await self.ex.close()
            await self.ex.load_markets()

    def start(self) -> list:
        return [asyncio.create_task(self._watch(s), name=f"ws:{s}") for s in self.symbols]

    async def _watch(self, sym: str) -> None:
        backoff = 1.0
        while True:
            try:
                limit = orderbook_limit(self.ex.id, self.depth)
                ob = await asyncio.wait_for(self.ex.watch_order_book(sym, limit), timeout=STREAM_IDLE_TIMEOUT_S)
                invalid_reason = validate_stream_order_book(ob)
                if invalid_reason is not None:
                    self.log("warn", f"{sym} stream snapshot rejected ({invalid_reason}); awaiting a fresh valid update")
                    await asyncio.sleep(min(backoff, 2.0))
                    continue
                self.books[sym] = Book(ob["bids"][: self.depth], ob["asks"][: self.depth], time.monotonic(),
                                        ob.get("timestamp"), ob.get("nonce"))
                self.dirty.add(sym)
                self.event.set()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log("warn", f"{sym} stream error {type(e).__name__}; retry {backoff:.0f}s")
                try:
                    await self._recover_connection()
                except asyncio.CancelledError:
                    raise
                except Exception as recovery_error:
                    self.log("error", f"{sym} stream recovery failed ({type(recovery_error).__name__}); feed remains untrusted")
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

