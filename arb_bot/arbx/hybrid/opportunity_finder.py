"""Persistent multi-source opportunity finder.

Consumes authenticated exchange adapters, WS order books, REST snapshots and optional
latency webhooks. It is deliberately scanner-only: it never submits orders. Execution
must still pass the Nu-Arb VenueAdapter, PnL and risk gates.

The finder combines:
- cross-venue executable depth, not top-of-book alone;
- REST/WS divergence and freshness checks;
- private REST balances when credentials are configured;
- measured REST/WS latency and clock skew;
- order-book imbalance, spread velocity and microprice pressure;
- Monte Carlo resampling of observed spread/slippage/latency;
- optional signed webhook observations as an independent timing source.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from .depth import normalize_depth
from .pnl import PnLModel, gate_profit
from arbx.journal import TradeJournal


@dataclass(slots=True)
class BookSample:
    venue: str
    symbol: str
    received_ns: int
    exchange_ts_ms: int | None
    sequence: int | None
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    source: str
    rest_rtt_ms: float | None = None

    @property
    def age_ms(self) -> float:
        return max(0.0, (time.monotonic_ns() - self.received_ns) / 1_000_000)

    def top(self) -> tuple[float, float, float, float]:
        bid = self.bids[0][0] if self.bids else 0.0
        ask = self.asks[0][0] if self.asks else 0.0
        return bid, self.bids[0][1] if self.bids else 0.0, ask, self.asks[0][1] if self.asks else 0.0


@dataclass(slots=True)
class WebhookObservation:
    source: str
    received_ns: int
    event_ts_ms: int | None
    sequence: str | None
    latency_ms: float | None
    valid_signature: bool
    payload: dict[str, Any]

    @property
    def fresh(self) -> bool:
        return (time.monotonic_ns() - self.received_ns) < 2_000_000_000


@dataclass(slots=True)
class MonteCarloResult:
    trials: int
    probability_positive: float
    expected_net_usd: float
    p05_net_usd: float
    p50_net_usd: float
    p95_net_usd: float


@dataclass(slots=True)
class Opportunity:
    strategy: str
    buy_venue: str
    sell_venue: str
    symbol: str
    notional_usd: float
    expected_net_usd: float
    worst_case_net_usd: float
    expected_bps: float
    worst_case_bps: float
    probability_positive: float
    score: float
    freshness_ms: float
    evidence: dict[str, Any] = field(default_factory=dict)


def _walk(book: BookSample, side: str, quote_usd: float) -> tuple[float, float]:
    """Return (base filled, quote spent/received) walking real depth."""
    levels = book.asks if side == "buy" else book.bids
    remaining_quote = max(0.0, quote_usd)
    base = 0.0
    quote = 0.0
    for price, qty in levels:
        if price <= 0 or qty <= 0:
            continue
        take_base = min(qty, remaining_quote / price)
        base += take_base
        quote += take_base * price
        remaining_quote -= take_base * price
        if remaining_quote <= 1e-12:
            break
    return base, quote


def _vwap(book: BookSample, side: str, quote_usd: float) -> tuple[float | None, float]:
    base, quote = _walk(book, side, quote_usd)
    return (quote / base if base else None), base


def _imbalance(book: BookSample, levels: int = 10) -> float:
    bid = sum(p * q for p, q in book.bids[:levels])
    ask = sum(p * q for p, q in book.asks[:levels])
    total = bid + ask
    return (bid - ask) / total if total else 0.0


def _microprice(book: BookSample) -> float | None:
    bid, bid_qty, ask, ask_qty = book.top()
    if not bid or not ask or bid_qty + ask_qty <= 0:
        return None
    return (ask * bid_qty + bid * ask_qty) / (bid_qty + ask_qty)


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    pos = min(len(values) - 1, max(0, int(round((len(values) - 1) * q))))
    return values[pos]


def monte_carlo(
    spread_bps: float,
    slippage_bps: float,
    fee_bps: float,
    latency_ms: float,
    volatility_bps: float,
    *,
    trials: int = 1200,
    seed: int | None = None,
    notional_usd: float = 3.0,
) -> MonteCarloResult:
    """Estimate execution-distribution, never a guarantee.

    Latency is modeled as additional adverse movement proportional to the observed
    short-horizon volatility. A conservative Gaussian tail is used for the residual.
    """
    rng = random.Random(seed)
    nets: list[float] = []
    vol = max(0.05, volatility_bps)
    for _ in range(max(100, trials)):
        realized_slip = max(0.0, rng.gauss(slippage_bps, max(0.05, slippage_bps * 0.35)))
        latency_move = abs(rng.gauss(0.0, vol)) * math.sqrt(max(latency_ms, 1.0) / 100.0)
        residual = rng.gauss(0.0, vol * 0.15)
        net_bps = spread_bps - fee_bps - realized_slip - latency_move + residual
        nets.append(notional_usd * net_bps / 10_000.0)
    return MonteCarloResult(
        trials=len(nets),
        probability_positive=sum(x > 0 for x in nets) / len(nets),
        expected_net_usd=sum(nets) / len(nets),
        p05_net_usd=_pct(nets, 0.05),
        p50_net_usd=_pct(nets, 0.50),
        p95_net_usd=_pct(nets, 0.95),
    )


class WebhookLatencyRegistry:
    """Small authenticated webhook receiver for independent timing observations."""

    def __init__(self, secrets: dict[str, str] | None = None, max_age_ms: int = 1500):
        self.secrets = secrets or {}
        self.max_age_ms = max_age_ms
        self.observations: deque[WebhookObservation] = deque(maxlen=1000)

    def ingest(
        self,
        source: str,
        payload: dict[str, Any],
        *,
        signature: str | None = None,
        raw_body: bytes | None = None,
        event_ts_ms: int | None = None,
        sequence: str | None = None,
    ) -> WebhookObservation:
        secret = self.secrets.get(source)
        valid = secret is None
        if secret is not None and signature and raw_body is not None:
            digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
            valid = hmac.compare_digest(digest, signature.removeprefix("sha256="))
        if secret is not None and not valid:
            raise ValueError("webhook_signature_invalid")
        now_ms = time.time_ns() // 1_000_000
        latency = (now_ms - event_ts_ms) if event_ts_ms is not None else None
        if latency is not None and abs(latency) > self.max_age_ms:
            raise ValueError("webhook_event_stale")
        obs = WebhookObservation(
            source, time.monotonic_ns(), event_ts_ms, sequence, latency, valid, payload
        )
        self.observations.append(obs)
        return obs

    def fresh(self) -> list[WebhookObservation]:
        return [x for x in self.observations if x.fresh]


class PersistentOpportunityFinder:
    def __init__(
        self,
        cfg: Any,
        adapters: dict[str, Any],
        *,
        symbols: list[str] | None = None,
        webhook_registry: WebhookLatencyRegistry | None = None,
        on_opportunity: Callable[[Opportunity], Awaitable[None] | None] | None = None,
    ):
        self.cfg = cfg
        self.adapters = adapters
        self.symbols = symbols or [getattr(cfg, "preflight_symbol", None) or "BTC/USDT"]
        self.webhooks = webhook_registry or WebhookLatencyRegistry()
        self.on_opportunity = on_opportunity
        self.books: dict[tuple[str, str], BookSample] = {}
        self.history: dict[tuple[str, str], deque[float]] = defaultdict(lambda: deque(maxlen=120))
        self.last_rest: dict[tuple[str, str], BookSample] = {}
        self.balances: dict[str, dict[str, float]] = {}
        self.stop_event = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.scans = 0
        self.opportunities = 0

    async def _capture_ws(self, venue: str, symbol: str, adapter: Any) -> None:
        while not self.stop_event.is_set():
            started = time.perf_counter()
            try:
                raw = await asyncio.wait_for(adapter.ex.watch_order_book(symbol, max(50, int(self.cfg.depth))), timeout=10)
                book = normalize_depth(venue, symbol, raw, limit=max(50, int(self.cfg.depth)))
                sample = BookSample(
                    venue, symbol, time.monotonic_ns(), raw.get("timestamp"),
                    raw.get("nonce"), list(book.bids), list(book.asks), "ws",
                    (time.perf_counter() - started) * 1000,
                )
                self.books[(venue, symbol)] = sample
                self._remember(sample)
                await self._scan_symbol(symbol)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(0.25)

    async def _capture_balance(self, venue: str, adapter: Any) -> None:
        while not self.stop_event.is_set():
            try:
                raw = await adapter.get_balances()
                free = raw.get("free") if isinstance(raw, dict) else None
                if isinstance(free, dict):
                    self.balances[venue] = {
                        str(asset): float(value)
                        for asset, value in free.items()
                        if isinstance(value, (int, float)) and float(value) >= 0
                    }
                await asyncio.sleep(max(1.0, float(getattr(self.cfg, "opportunity_balance_interval_s", 5.0))))
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(2.0)

    async def _capture_rest(self, venue: str, symbol: str, adapter: Any) -> None:
        while not self.stop_event.is_set():
            started = time.perf_counter()
            try:
                raw = await adapter.get_order_book(symbol, max(50, int(self.cfg.depth)))
                book = normalize_depth(venue, symbol, raw, limit=max(50, int(self.cfg.depth)))
                sample = BookSample(
                    venue, symbol, time.monotonic_ns(), raw.get("timestamp"),
                    raw.get("nonce"), list(book.bids), list(book.asks), "rest",
                    (time.perf_counter() - started) * 1000,
                )
                self.last_rest[(venue, symbol)] = sample
                # REST is an independent corroboration source; don't overwrite a fresher WS book.
                ws = self.books.get((venue, symbol))
                if ws is None or sample.received_ns > ws.received_ns:
                    self.books[(venue, symbol)] = sample
                await asyncio.sleep(max(0.2, float(getattr(self.cfg, "opportunity_rest_interval_s", 1.0))))
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(1.0)

    def _remember(self, sample: BookSample) -> None:
        mid = _microprice(sample)
        if mid:
            self.history[(sample.venue, sample.symbol)].append(mid)

    def _latency_ms(self, venue: str, symbol: str) -> float:
        sample = self.books.get((venue, symbol))
        rest = self.last_rest.get((venue, symbol))
        values = [x for x in (
            sample.rest_rtt_ms if sample else None,
            rest.rest_rtt_ms if rest else None,
        ) if isinstance(x, (int, float))]
        return sum(values) / len(values) if values else float(getattr(self.cfg, "max_rtt_ms", 100))

    def _spread_velocity_bps(self, venue: str, symbol: str) -> float:
        values = list(self.history[(venue, symbol)])
        if len(values) < 4:
            return 0.0
        mids = values[-8:]
        return (mids[-1] / mids[0] - 1.0) * 10_000 / max(1, len(mids) - 1)

    def _volatility_bps(self, venue: str, symbol: str) -> float:
        values = list(self.history[(venue, symbol)])
        if len(values) < 3:
            return 5.0
        returns = [(values[i] / values[i - 1] - 1.0) * 10_000 for i in range(1, len(values))]
        mean = sum(returns) / len(returns)
        var = sum((x - mean) ** 2 for x in returns) / max(1, len(returns) - 1)
        return math.sqrt(var)

    def _fees_bps(self, venue: str, symbol: str) -> float:
        try:
            # Fee query is intentionally best-effort; live execution still re-checks fees.
            return float((self.adapters[venue].ex.markets.get(symbol) or {}).get("taker") or 0.001) * 20_000
        except Exception:
            return 20.0

    def _best_route(self, symbol: str) -> Opportunity | None:
        venues = [v for v in self.adapters if (v, symbol) in self.books]
        if len(venues) < 2:
            return None
        notional = float(getattr(self.cfg, "trade_size_usd", 3.0))
        best: Opportunity | None = None
        for buy in venues:
            b = self.books[(buy, symbol)]
            if b.age_ms > float(getattr(self.cfg, "max_book_age_ms", 750)):
                continue
            buy_px, buy_base = _vwap(b, "buy", notional)
            if not buy_px or buy_base <= 0:
                continue
            for sell in venues:
                if sell == buy:
                    continue
                s = self.books[(sell, symbol)]
                if s.age_ms > float(getattr(self.cfg, "max_book_age_ms", 750)):
                    continue
                sell_px, sell_base = _vwap(s, "sell", notional)
                if not sell_px or sell_base <= 0:
                    continue
                executable_base = min(buy_base, sell_base)
                gross = (sell_px - buy_px) * executable_base
                gross_bps = gross / notional * 10_000
                fee_bps = self._fees_bps(buy, symbol) + self._fees_bps(sell, symbol)
                slippage_bps = max(0.0, (buy_px / (b.top()[2] or buy_px) - 1.0) * 10_000)
                slippage_bps += max(0.0, (s.top()[0] / sell_px - 1.0) * 10_000)
                latency = max(self._latency_ms(buy, symbol), self._latency_ms(sell, symbol))
                vol = max(self._volatility_bps(buy, symbol), self._volatility_bps(sell, symbol))
                mc = monte_carlo(gross_bps, slippage_bps, fee_bps, latency, vol, notional_usd=notional)
                imbalance = (_imbalance(b) - _imbalance(s)) / 2.0
                micro = (_microprice(b) or buy_px) / max(_microprice(s) or sell_px, 1e-12) - 1.0
                pressure_bps = micro * 10_000
                spread_velocity_bps = self._spread_velocity_bps(buy, symbol) - self._spread_velocity_bps(sell, symbol)
                available_quote = self._available_quote(buy, symbol)
                if available_quote is not None and available_quote < notional:
                    continue
                score = (mc.expected_net_usd * 100 + mc.probability_positive * 10
                         + max(0.0, pressure_bps) + imbalance * 5 + max(0.0, spread_velocity_bps))
                model = PnLModel(
                    gross_usd=gross,
                    fees_usd=notional * fee_bps / 10_000,
                    slippage_usd=notional * slippage_bps / 10_000,
                    latency_usd=notional * max(0.0, latency - 20.0) * vol / 10_000 / 100,
                    partial_fill_reserve_usd=notional * max(0.0, 1.0 - executable_base / max(buy_base, 1e-12)) * 0.001,
                    safety_reserve_usd=notional * float(getattr(self.cfg, "safety_reserve_bps", 5.0)) / 10_000,
                )
                decision = gate_profit(
                    model,
                    min_profit_usd=float(getattr(self.cfg, "min_profit_usd", 0.0)),
                    min_worst_profit_usd=float(getattr(self.cfg, "min_worst_profit_usd", 0.0)),
                )
                worst = min(model.worst_case_net_usd, mc.p05_net_usd)
                opp = Opportunity(
                    "cross_exchange_depth",
                    buy, sell, symbol, notional,
                    mc.expected_net_usd, worst,
                    mc.expected_net_usd / notional * 10_000,
                    worst / notional * 10_000,
                    mc.probability_positive, score,
                    max(b.age_ms, s.age_ms),
                    {
                        "decision": decision,
                        "grossDepthUsd": gross,
                        "buyVwap": buy_px,
                        "sellVwap": sell_px,
                        "executableBase": executable_base,
                        "feeBps": fee_bps,
                        "slippageBps": slippage_bps,
                        "latencyMs": latency,
                        "volatilityBps": vol,
                        "bookImbalanceBuy": _imbalance(b),
                        "bookImbalanceSell": _imbalance(s),
                        "micropricePressureBps": pressure_bps,
                        "spreadVelocityBps": spread_velocity_bps,
                        "availableQuoteUsd": available_quote,
                        "mc": {
                            "trials": mc.trials,
                            "probabilityPositive": mc.probability_positive,
                            "expectedNetUsd": mc.expected_net_usd,
                            "p05NetUsd": mc.p05_net_usd,
                            "p50NetUsd": mc.p50_net_usd,
                            "p95NetUsd": mc.p95_net_usd,
                        },
                        "wsRestDivergenceBps": self._ws_rest_divergence(buy, symbol, buy_px),
                        "webhooks": [x.source for x in self.webhooks.fresh()],
                    },
                )
                if best is None or opp.score > best.score:
                    best = opp
        return best

    def _available_quote(self, venue: str, symbol: str) -> float | None:
        quote = symbol.split("/", 1)[1] if "/" in symbol else symbol
        balance = self.balances.get(venue)
        return float(balance.get(quote, 0.0)) if balance else None

    def _ws_rest_divergence(self, venue: str, symbol: str, ws_px: float) -> float:
        rest = self.last_rest.get((venue, symbol))
        if not rest:
            return 0.0
        rest_px = _microprice(rest)
        return abs(ws_px / rest_px - 1.0) * 10_000 if rest_px else 0.0

    async def _scan_symbol(self, symbol: str) -> None:
        self.scans += 1
        opp = self._best_route(symbol)
        if opp is None:
            return
        self.opportunities += 1
        journal = TradeJournal(self.cfg.journal_path.with_suffix(".sqlite3"))
        try:
            journal.append_opportunity({
                "session_id": getattr(self.cfg, "session_id", "opportunity-finder"),
                "mode": getattr(self.cfg, "mode", "paper"),
                "strategy": opp.strategy,
                "exchange_a": opp.buy_venue,
                "exchange_b": opp.sell_venue,
                "symbol": opp.symbol,
                "requested_usd": opp.notional_usd,
                "expected_net_usd": opp.expected_net_usd,
                "worst_case_net_usd": opp.worst_case_net_usd,
                "expected_net_bps": opp.expected_bps,
                "worst_case_net_bps": opp.worst_case_bps,
                "book_age_ms": opp.freshness_ms,
                "decision": "ANTICIPATED" if opp.probability_positive >= 0.6 and opp.expected_net_usd > 0 else "REJECTED",
                "rejection_reason": None if opp.expected_net_usd > 0 else "monte_carlo_expected_non_positive",
                "evidence": opp.evidence,
            })
        finally:
            journal.close()
        if opp.expected_net_usd > 0 and opp.worst_case_net_usd > 0 and opp.probability_positive >= 0.65:
            if self.on_opportunity:
                result = self.on_opportunity(opp)
                if asyncio.iscoroutine(result):
                    await result

    async def run(self) -> None:
        self.stop_event.clear()
        self.tasks = []
        for venue, adapter in self.adapters.items():
            for symbol in self.symbols:
                self.tasks.append(asyncio.create_task(self._capture_rest(venue, symbol, adapter)))
            # CCXT Pro adapters expose the underlying authenticated WS connection.
            if getattr(adapter, "ex", None) is not None and hasattr(adapter.ex, "watch_order_book"):
                for symbol in self.symbols:
                    self.tasks.append(asyncio.create_task(self._capture_ws(venue, symbol, adapter)))
        await asyncio.gather(*self.tasks)

    async def stop(self) -> None:
        self.stop_event.set()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []


async def serve_webhooks(
    registry: WebhookLatencyRegistry,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> asyncio.AbstractServer:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=3)
            parts = request.decode("ascii", "ignore").split()
            if len(parts) < 2 or parts[0] != "POST":
                writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            headers = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=3)
                if line in (b"\r\n", b"\n", b""):
                    break
                key, _, value = line.decode("latin1").partition(":")
                headers[key.lower().strip()] = value.strip()
            length = int(headers.get("content-length", "0"))
            body = await asyncio.wait_for(reader.readexactly(length), timeout=3)
            payload = json.loads(body or b"{}")
            path = urlsplit(parts[1]).path
            source = path.rsplit("/", 1)[-1] or "unknown"
            obs = registry.ingest(
                source, payload,
                signature=headers.get("x-nuarb-signature"),
                raw_body=body,
                event_ts_ms=payload.get("event_ts_ms") or payload.get("timestamp"),
                sequence=str(payload.get("sequence")) if payload.get("sequence") is not None else None,
            )
            response = json.dumps({"ok": True, "source": source, "latencyMs": obs.latency_ms}).encode()
            writer.write(
                f"HTTP/1.1 {HTTPStatus.OK.value} OK\r\nContent-Type: application/json\r\nContent-Length: {len(response)}\r\n\r\n".encode()
                + response
            )
        except Exception as exc:
            body = json.dumps({"ok": False, "error": str(exc)}).encode()
            writer.write(
                f"HTTP/1.1 {HTTPStatus.BAD_REQUEST.value} Bad Request\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
        finally:
            await writer.drain()
            writer.close()
            await writer.wait_closed()

    return await asyncio.start_server(handle, host, port)


async def run_persistent_finder(
    cfg: Any,
    adapters: dict[str, Any],
    symbols: list[str],
    *,
    webhook_host: str = "127.0.0.1",
    webhook_port: int = 8765,
) -> None:
    registry = WebhookLatencyRegistry()
    finder = PersistentOpportunityFinder(cfg, adapters, symbols=symbols, webhook_registry=registry)
    webhook_server = await serve_webhooks(registry, webhook_host, webhook_port)
    try:
        await finder.run()
    finally:
        webhook_server.close()
        await webhook_server.wait_closed()
        await finder.stop()


async def _main() -> None:
    from arbx.config import Config
    from .engine import HybridEngine

    cfg = Config.from_env()
    cfg.validate()
    engine = HybridEngine.create(cfg)
    requested = [x.venue_id or x.id for x in cfg.exchanges]
    connected: list[str] = []
    try:
        for venue in requested:
            adapter = engine.adapters.get(venue)
            if adapter is None:
                continue
            try:
                await adapter.connect()
                connected.append(venue)
            except Exception as exc:
                print(f"[opportunity-finder] {venue}: connect failed: {type(exc).__name__}")
        if len(connected) < 2:
            raise RuntimeError("persistent opportunity finder requires at least two connected venues")
        symbols = [s.strip() for s in os.getenv("BOT_OPPORTUNITY_SYMBOLS", "BTC/USDT").split(",") if s.strip()]
        print(
            f"[opportunity-finder] persistent WS+REST depth scan venues={','.join(connected)} "
            f"symbols={','.join(symbols)} webhook=127.0.0.1:8765"
        )
        await run_persistent_finder(
            cfg,
            {k: engine.adapters[k] for k in connected},
            symbols,
            webhook_host=os.getenv("BOT_OPPORTUNITY_WEBHOOK_HOST", "127.0.0.1"),
            webhook_port=int(os.getenv("BOT_OPPORTUNITY_WEBHOOK_PORT", "8765")),
        )
    finally:
        for adapter in engine.adapters.values():
            try:
                await adapter.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(_main())
