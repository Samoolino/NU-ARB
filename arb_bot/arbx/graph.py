"""Market graph -> triangular cycle discovery over EVERY spot market of an exchange, ranked by
liquidity tier first and by lowest fees second (the 'low-fee vehicle' selection), then trimmed to a stream budget."""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from arbx.util import STABLES


@dataclass(frozen=True, slots=True)
class Leg:
    symbol: str
    side: str   # "buy": spend quote, receive base | "sell": spend base, receive quote


@dataclass(frozen=True, slots=True)
class Triangle:
    start: str
    legs: tuple
    name: str
    fee_bps: float   # sum of the three legs' taker fees: the cost of this vehicle
    score: float     # USD 24h volume of the thinnest leg


def usd_volume(m: dict, t: dict | None, tickers: dict) -> float:
    qv = (t or {}).get("quoteVolume")
    if not qv:
        return 0.0
    q = m["quote"]
    if q in STABLES:
        return float(qv)
    px = (tickers.get(f"{q}/USDT") or {}).get("last")
    return float(qv) * float(px) if px else 0.0


def discover(markets: dict, tickers: dict, starts, max_symbols: int, default_taker_bps: float):
    spot = {s: m for s, m in markets.items()
            if m.get("spot") and m.get("active") is not False and m.get("base") and m.get("quote")}
    vol = {s: (usd_volume(m, tickers.get(s), tickers) if tickers else 1.0) for s, m in spot.items()}
    adj = defaultdict(list)
    for s, m in spot.items():
        adj[m["quote"]].append((m["base"], s, "buy"))    # quote -> base
        adj[m["base"]].append((m["quote"], s, "sell"))   # base  -> quote
    tris, seen = [], set()
    for a in starts:
        for b, s1, d1 in adj.get(a, ()):
            if vol[s1] <= 0:
                continue
            for c, s2, d2 in adj.get(b, ()):
                if c == a or s2 == s1 or vol[s2] <= 0:
                    continue
                for a2, s3, d3 in adj.get(c, ()):
                    if a2 != a or s3 in (s1, s2) or vol[s3] <= 0:
                        continue
                    key = (s1, d1, s2, d2, s3, d3)
                    if key in seen:
                        continue
                    seen.add(key)
                    legs = (Leg(s1, d1), Leg(s2, d2), Leg(s3, d3))
                    fee = sum((spot[l.symbol].get("taker") if spot[l.symbol].get("taker") is not None
                               else default_taker_bps / 1e4) * 1e4 for l in legs)
                    tris.append(Triangle(a, legs, f"{a}>{b}>{c}>{a}", fee, min(vol[l.symbol] for l in legs)))

    # liquidity tier (log10 buckets of 0.25 decade) first, cheapest fee vehicle second
    tris.sort(key=lambda t: (-round(math.log10(t.score + 1.0) * 4), t.fee_bps))
    chosen, syms = [], set()
    for t in tris:
        need = {l.symbol for l in t.legs} - syms
        if len(syms) + len(need) > max_symbols:
            continue
        syms |= need
        chosen.append(t)
    return chosen, syms, vol
