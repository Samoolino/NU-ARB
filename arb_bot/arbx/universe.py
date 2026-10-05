"""Global spot-market universe discovery and adaptive hot-market selection.

Discovery is intentionally broader than streaming: every discovered spot market is
indexed, while only high-value markets are promoted into the real-time order-book
set. This prevents the false choice between "all markets" and "subscribe to
thousands of books".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


STABLE_QUOTES = frozenset({"USDT", "USDC", "DAI"})


@dataclass(frozen=True)
class MarketRecord:
    symbol: str
    base: str
    quote: str
    active: bool
    spot: bool
    contract: bool
    quote_volume: float
    liquidity_score: float


def _number(value: Any) -> float:
    try:
        result = float(value)
        return result if result == result and result > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def build_market_universe(markets: dict, tickers: dict | None = None) -> dict[str, MarketRecord]:
    """Index every active spot market exposed by an exchange adapter."""
    tickers = tickers or {}
    universe: dict[str, MarketRecord] = {}
    for symbol, market in markets.items():
        if not isinstance(market, dict):
            continue
        if not market.get("spot") or market.get("contract") or market.get("active") is False:
            continue
        base, quote = market.get("base"), market.get("quote")
        if not base or not quote:
            continue
        ticker = tickers.get(symbol) or {}
        quote_volume = _number(ticker.get("quoteVolume"))
        if quote_volume <= 0:
            last = _number(ticker.get("last"))
            base_volume = _number(ticker.get("baseVolume"))
            quote_volume = last * base_volume if last and base_volume else 0.0
        # Volume is deliberately a ranking signal, not an execution guarantee.
        universe[symbol] = MarketRecord(
            symbol=symbol,
            base=str(base),
            quote=str(quote),
            active=True,
            spot=True,
            contract=False,
            quote_volume=quote_volume,
            liquidity_score=quote_volume,
        )
    return universe


def select_hot_markets(
    universe: dict[str, MarketRecord],
    *,
    max_symbols: int,
    preferred_quotes: frozenset[str] = STABLE_QUOTES,
) -> list[str]:
    """Return the bounded real-time set while keeping the complete universe indexed."""
    ranked = [
        record for record in universe.values()
        if record.quote in preferred_quotes
    ]
    ranked.sort(key=lambda record: (record.liquidity_score, record.symbol), reverse=True)
    return [record.symbol for record in ranked[:max(1, int(max_symbols))]]


def universe_stats(
    universe: dict[str, MarketRecord],
    hot_symbols: set[str] | list[str] | None = None,
) -> dict:
    hot = set(hot_symbols or ())
    stable = sum(record.quote in STABLE_QUOTES for record in universe.values())
    bases = {record.base for record in universe.values()}
    quotes = {record.quote for record in universe.values()}
    return {
        "discoveredMarkets": len(universe),
        "spotMarkets": len(universe),
        "stableQuoteMarkets": stable,
        "uniqueBaseAssets": len(bases),
        "uniqueQuoteAssets": len(quotes),
        "hotMarkets": len(hot & set(universe)),
        "observationModel": "adaptive_hot_set",
    }
