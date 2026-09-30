"""Opportunity evaluation. Pure functions: books in -> Opp out. Every Opp already carries the IOC limit price
per leg and the WORST-CASE result if every leg fills at its limit (the basis of the no-loss guarantee)."""
from __future__ import annotations

from dataclasses import dataclass

from arbx.util import buy_with_quote, sell_base, walk_base


@dataclass(slots=True)
class Opp:
    name: str
    start_asset: str
    start: float
    expected_final: float
    worst_final: float
    net_bps: float
    worst_bps: float
    age_ms: float
    legs: tuple
    limits: tuple
    path: tuple      # ((amount_in, amount_out) per leg)


@dataclass(slots=True)
class CrossOpp:
    symbol: str
    buy_ex: str
    sell_ex: str
    base: float
    limit_buy: float
    limit_sell: float
    cost: float
    expected_usd: float
    worst_usd: float
    net_bps: float
    worst_bps: float
    age_ms: float
    base_ccy: str
    quote_ccy: str


def evaluate_triangle(tri, books, size, fee_of, min_net_bps, tol_bps, round_px, now):
    amt, path, lasts, age = size, [], [], 0.0
    for leg in tri.legs:
        b = books.get(leg.symbol)
        if b is None or not b.bids or not b.asks:
            return None
        a = (now - b.recv) * 1000.0
        age = a if a > age else age
        r = buy_with_quote(b.asks, amt) if leg.side == "buy" else sell_base(b.bids, amt)
        if r is None:
            return None
        out, last = r
        path.append((amt, out))
        lasts.append(last)
        amt = out * (1.0 - fee_of(leg.symbol))
    net_bps = (amt / size - 1.0) * 1e4
    if net_bps < min_net_bps:            # cheap early exit: the overwhelming majority of scans end here
        return None
    tol = tol_bps / 1e4
    limits = tuple(round_px(l.symbol, p * (1.0 + tol) if l.side == "buy" else p * (1.0 - tol))
                   for l, p in zip(tri.legs, lasts))
    w = size
    for l, lim in zip(tri.legs, limits):
        w = w / lim if l.side == "buy" else w * lim
        w *= 1.0 - fee_of(l.symbol)
    return Opp(tri.name, tri.start, size, amt, w, net_bps, (w / size - 1.0) * 1e4, age,
               tri.legs, limits, tuple(path))


def evaluate_cross(sym, bw, sw, bb, sb, size_usd, cfg, now):
    """Buy `sym` on worker bw (book bb), sell on worker sw (book sb). Inventory is pre-funded on both sides."""
    if not bb.asks or not sb.bids:
        return None
    age = max(now - bb.recv, now - sb.recv) * 1000.0
    if age > cfg.max_book_age_ms:
        return None
    base = size_usd / bb.asks[0][0]
    c, s = walk_base(bb.asks, base), walk_base(sb.bids, base)
    if c is None or s is None:
        return None
    cost, last_a = c
    proceeds, last_b = s
    fb, fs = bw.fee_of(sym), sw.fee_of(sym)
    net = proceeds * (1.0 - fs) - cost * (1.0 + fb)
    net_bps = net / cost * 1e4 - cfg.rebalance_haircut_bps
    if net_bps < cfg.min_net_bps:
        return None
    tol = cfg.limit_tol_bps / 1e4
    lim_buy = bw.round_price(sym, last_a * (1.0 + tol))
    lim_sell = sw.round_price(sym, last_b * (1.0 - tol))
    worst_cost = base * lim_buy
    worst_net = base * lim_sell * (1.0 - fs) - worst_cost * (1.0 + fb) - worst_cost * cfg.rebalance_haircut_bps / 1e4
    m = bw.ex.markets[sym]
    return CrossOpp(sym, bw.id, sw.id, base, lim_buy, lim_sell, cost, net, worst_net, net_bps,
                    worst_net / worst_cost * 1e4, age, m["base"], m["quote"])
