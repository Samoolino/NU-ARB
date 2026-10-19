"""Best-effort CoinGecko USD reference prices; never used as executable venue quotes."""
from __future__ import annotations

import asyncio
import json
import math
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

COIN_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "BNB": "binancecoin", "DOGE": "dogecoin", "ADA": "cardano", "TRX": "tron",
    "LTC": "litecoin", "BCH": "bitcoin-cash", "LINK": "chainlink", "DOT": "polkadot",
    "AVAX": "avalanche-2", "SHIB": "shiba-inu", "TON": "the-open-network",
}
_CACHE: dict[str, object] = {"expires": 0.0, "data": {}}
_CACHE_TTL_SECONDS = 45.0
_MAX_REFERENCE_AGE_SECONDS = 180.0


def _fetch(ids: list[str]) -> dict:
    query = urlencode({"ids": ",".join(ids), "vs_currencies": "usd", "include_last_updated_at": "true"})
    request = Request(
        f"https://api.coingecko.com/api/v3/simple/price?{query}",
        headers={"Accept": "application/json", "User-Agent": "NU-ARB-readonly-reference/1.0"},
    )
    with urlopen(request, timeout=3.0) as response:
        if response.status != 200:
            raise RuntimeError("CoinGecko reference request failed")
        payload = json.loads(response.read(512_000))
    now = time.time()
    output = {}
    for symbol, coin_id in COIN_IDS.items():
        row = payload.get(coin_id)
        if not isinstance(row, dict):
            continue
        try:
            price = float(row["usd"])
            updated_at = float(row["last_updated_at"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        age = now - updated_at
        if not math.isfinite(price) or price <= 0 or not math.isfinite(age) or age < -30 or age > _MAX_REFERENCE_AGE_SECONDS:
            continue
        output[symbol] = {"priceUsd": price, "updatedAt": updated_at, "ageSeconds": max(0.0, age)}
    return output


async def get_reference_prices(symbols: list[str]) -> dict:
    """Fetch a bounded, cached CoinGecko reference snapshot, failing soft on outages."""
    requested_pairs = sorted({symbol for symbol in symbols if "/" in symbol
                              and symbol.split("/", 1)[0] in COIN_IDS
                              and symbol.split("/", 1)[1] in {"USDT", "USDC", "DAI"}})
    wanted = sorted({symbol.split("/", 1)[0] for symbol in requested_pairs})
    if not wanted:
        return {"status": "unavailable", "provider": "CoinGecko", "prices": {}, "ageSeconds": None}
    now = time.monotonic()
    cached = _CACHE.get("data")
    if now >= float(_CACHE.get("expires", 0.0)) or not isinstance(cached, dict):
        try:
            fresh = await asyncio.wait_for(asyncio.to_thread(_fetch, sorted(set(COIN_IDS.values()))), timeout=4.0)
            _CACHE["data"] = fresh
            _CACHE["expires"] = now + _CACHE_TTL_SECONDS
            cached = fresh
        except Exception:
            if not isinstance(cached, dict) or not cached:
                return {"status": "unavailable", "provider": "CoinGecko", "prices": {}, "ageSeconds": None}
    prices = {}
    ages = []
    now_wall = time.time()
    for pair in requested_pairs:
        entry = cached.get(pair.split("/", 1)[0])
        if not isinstance(entry, dict):
            continue
        try:
            updated_at = float(entry["updatedAt"])
            price = float(entry["priceUsd"])
            age = now_wall - updated_at
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(price) or price <= 0 or not math.isfinite(age) or age < -30 or age > _MAX_REFERENCE_AGE_SECONDS:
            continue
        prices[pair] = price
        ages.append(max(0.0, age))
    return {"status": "available" if prices else "unavailable", "provider": "CoinGecko", "prices": prices,
            "ageSeconds": round(max(ages), 1) if ages else None}
