"""Read-only multi-venue spot order-book snapshots and modeled cross-venue edges."""
from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Callable

from arbx.config import CCXT_ADAPTERS
from arbx.web_api import VENUES

STABLE_QUOTES = frozenset({"USDT", "USDC", "DAI"})
MAX_SCAN_SYMBOLS = 12
MAX_PUBLIC_NOTIONAL_USD = 100_000.0
MAX_TAKER_FEE_BPS = 100.0
MAX_MIN_NET_BPS = 500.0
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{2,20}/[A-Z0-9]{2,20}$")


def _positive_level(level) -> tuple[float, float] | None:
    if not isinstance(level, (list, tuple)) or len(level) < 2:
        return None
    try:
        price, amount = float(level[0]), float(level[1])
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(price) or not math.isfinite(amount) or price <= 0 or amount <= 0:
        return None
    return price, amount


def _walk_asks(asks, quote_budget: float) -> tuple[float, float, bool]:
    quote_left = quote_budget
    quantity = 0.0
    quote_spent = 0.0
    for level in asks:
        parsed = _positive_level(level)
        if parsed is None:
            continue
        price, amount = parsed
        bought = min(amount, quote_left / price)
        quantity += bought
        spent = bought * price
        quote_spent += spent
        quote_left -= spent
        if quote_left <= max(1e-9, quote_budget * 1e-12):
            return quantity, quote_spent, True
    return quantity, quote_spent, quote_left <= max(1e-8, quote_budget * 1e-8)


def _walk_bids(bids, quantity: float) -> tuple[float, bool]:
    left = quantity
    proceeds = 0.0
    for level in bids:
        parsed = _positive_level(level)
        if parsed is None:
            continue
        price, amount = parsed
        sold = min(amount, left)
        proceeds += sold * price
        left -= sold
        if left <= max(1e-12, quantity * 1e-12):
            return proceeds, True
    return proceeds, left <= max(1e-10, quantity * 1e-8)


def rank_snapshot_books(
    symbol: str,
    books: dict[str, dict],
    *,
    notional_usd: float,
    taker_fee_bps: float,
    min_net_bps: float,
) -> list[dict]:
    rows = []
    venue_ids = sorted(books)
    for buy_id in venue_ids:
        for sell_id in venue_ids:
            if buy_id == sell_id:
                continue
            buy_book, sell_book = books[buy_id], books[sell_id]
            quantity, buy_quote, buy_complete = _walk_asks(buy_book.get("asks", []), notional_usd)
            if not buy_complete or quantity <= 0:
                continue
            sell_quote, sell_complete = _walk_bids(sell_book.get("bids", []), quantity)
            if not sell_complete or buy_quote <= 0:
                continue
            buy_fee = buy_quote * taker_fee_bps / 10_000
            sell_fee = sell_quote * taker_fee_bps / 10_000
            net = sell_quote - sell_fee - buy_quote - buy_fee
            net_bps = net / buy_quote * 10_000
            rows.append({
                "symbol": symbol,
                "buyVenue": buy_id,
                "sellVenue": sell_id,
                "quantityBase": quantity,
                "buyVwap": buy_quote / quantity,
                "sellVwap": sell_quote / quantity,
                "buyNotionalUsd": buy_quote,
                "sellNotionalUsd": sell_quote,
                "estimatedFeesUsd": buy_fee + sell_fee,
                "netPnlUsd": net,
                "netBps": net_bps,
                "status": ("meets_minimum_estimated_edge" if net_bps >= min_net_bps
                           else "below_minimum_estimated_edge" if net > 0 else "no_net_edge"),
                "executionEnabled": False,
            })
    return sorted(rows, key=lambda item: (item["netPnlUsd"], item["netBps"]), reverse=True)


async def scan_public_spot(
    venue_ids: list[str],
    symbols: list[str],
    *,
    notional_usd: float,
    taker_fee_bps: float = 10.0,
    min_net_bps: float = 5.0,
    exchange_factory: Callable | None = None,
) -> dict:
    if (len(venue_ids) < 2 or len(venue_ids) > 18
            or len(set(venue_ids)) != len(venue_ids)
            or any(venue not in VENUES for venue in venue_ids)):
        raise ValueError("Select between two and 18 distinct venues.")
    if any(not isinstance(venue, str) or not re.fullmatch(r"[a-z0-9]+", venue) for venue in venue_ids):
        raise ValueError("Venue IDs must be lowercase registry IDs.")
    if len(symbols) < 1 or len(symbols) > MAX_SCAN_SYMBOLS or len(set(symbols)) != len(symbols):
        raise ValueError(f"Select between one and {MAX_SCAN_SYMBOLS} distinct spot markets.")
    if any(not isinstance(symbol, str) or not SYMBOL_PATTERN.fullmatch(symbol)
           or symbol.split("/", 1)[1] not in STABLE_QUOTES for symbol in symbols):
        raise ValueError("Use BASE/USDT, BASE/USDC, or BASE/DAI spot markets.")
    if not math.isfinite(notional_usd) or not 1 <= notional_usd <= MAX_PUBLIC_NOTIONAL_USD:
        raise ValueError(f"Notional must be between 1 and {MAX_PUBLIC_NOTIONAL_USD:g} USD.")
    if not math.isfinite(taker_fee_bps) or not 0 <= taker_fee_bps <= MAX_TAKER_FEE_BPS:
        raise ValueError(f"Taker fee must be between 0 and {MAX_TAKER_FEE_BPS:g} bps.")
    if not math.isfinite(min_net_bps) or not 0 <= min_net_bps <= MAX_MIN_NET_BPS:
        raise ValueError(f"Minimum estimated edge must be between 0 and {MAX_MIN_NET_BPS:g} bps.")

    if exchange_factory is None:
        import ccxt.pro as ccxtpro

        def exchange_factory(venue_id):
            adapter_id = CCXT_ADAPTERS.get(venue_id, venue_id)
            cls = getattr(ccxtpro, adapter_id, None)
            if cls is None:
                raise RuntimeError("No CCXT Pro adapter is installed for this venue.")
            options = {"defaultType": "spot"}
            if venue_id == "binance":
                options["fetchMarkets"] = {"types": ["spot"]}
            return cls({"enableRateLimit": True, "timeout": 8000, "options": options})

    semaphore = asyncio.Semaphore(6)

    async def load_venue(venue_id: str) -> dict:
        exchange = None
        started = time.perf_counter()
        try:
            async with semaphore:
                exchange = exchange_factory(venue_id)
                await asyncio.wait_for(exchange.load_markets(), timeout=20)
            markets = {}
            for symbol in symbols:
                market = exchange.markets.get(symbol)
                if (market and (market.get("spot") is True or market.get("type") == "spot")
                        and not market.get("contract")
                        and exchange.has.get("fetchOrderBook")):
                    markets[symbol] = market
            if not markets:
                raise RuntimeError("No selected spot markets are listed by this venue.")

            async def fetch_book(symbol: str) -> tuple[str, dict | None, str | None]:
                try:
                    async with semaphore:
                        book = await asyncio.wait_for(exchange.fetch_order_book(symbol, 20), timeout=12)
                    asks = book.get("asks") if isinstance(book, dict) else None
                    bids = book.get("bids") if isinstance(book, dict) else None
                    if not asks or not bids:
                        raise RuntimeError("Empty spot order book.")
                    return symbol, {
                        "asks": asks,
                        "bids": bids,
                        "timestamp": book.get("timestamp"),
                    }, None
                except Exception as exc:
                    return symbol, None, type(exc).__name__

            fetched = await asyncio.gather(*(fetch_book(symbol) for symbol in markets))
            books = {symbol: book for symbol, book, _ in fetched if book is not None}
            errors = {symbol: error for symbol, _, error in fetched if error}
            return {
                "id": venue_id,
                "status": "available" if books else "unavailable",
                "latencyMs": round((time.perf_counter() - started) * 1000, 1),
                "markets": sorted(books),
                "books": books,
                "errors": errors,
            }
        except Exception as exc:
            return {
                "id": venue_id,
                "status": "unavailable",
                "latencyMs": round((time.perf_counter() - started) * 1000, 1),
                "markets": [],
                "books": {},
                "errors": {"venue": type(exc).__name__},
            }
        finally:
            if exchange is not None:
                try:
                    await exchange.close()
                except Exception:
                    pass

    results = await asyncio.gather(*(load_venue(venue) for venue in venue_ids))
    venues = []
    by_symbol: dict[str, dict[str, dict]] = {symbol: {} for symbol in symbols}
    for result in results:
        venues.append({key: value for key, value in result.items() if key != "books"})
        for symbol, book in result["books"].items():
            by_symbol[symbol][result["id"]] = book

    opportunities = []
    for symbol, books in by_symbol.items():
        opportunities.extend(rank_snapshot_books(
            symbol,
            books,
            notional_usd=notional_usd,
            taker_fee_bps=taker_fee_bps,
            min_net_bps=min_net_bps,
        ))
    opportunities.sort(key=lambda item: (item["netPnlUsd"], item["netBps"]), reverse=True)
    return {
        "observedAt": time.time(),
        "quoteValueAssumptionUsd": "USDT, USDC, and DAI treated as $1.00 for display",
        "dataMode": "public_rest_snapshot",
        "executionEnabled": False,
        "notionalUsd": notional_usd,
        "takerFeeBps": taker_fee_bps,
        "minimumNetBps": min_net_bps,
        "venues": venues,
        "opportunities": opportunities,
    }
