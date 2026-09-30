"""Transfer-vehicle planner: which token + chain moves value between exchanges cheapest (used for
REBALANCING inventory, never inside the trading loop)."""
from __future__ import annotations

from arbx.util import STABLES

CANDIDATES = ("USDT", "USDC", "TRX", "XRP", "XLM", "LTC", "BTC", "ETH", "SOL", "BNB")


async def rank_transfer_vehicles(ex, amount_usd: float, conv_bps: float = 20.0, candidates=CANDIDATES):
    """Rank (token, network) by total USD cost of moving `amount_usd`:
       flat withdrawal fee (USD) + round-trip conversion cost for non-stable vehicles (2 taker legs + slippage)."""
    cur = ex.currencies or await ex.fetch_currencies()
    tickers = await ex.fetch_tickers()
    out = []
    for code in candidates:
        nets = (cur.get(code) or {}).get("networks") or {}
        for nid, n in nets.items():
            if n.get("active") is False or n.get("withdraw") is False or n.get("deposit") is False:
                continue
            fee = n.get("fee")
            if fee is None:
                continue
            px = 1.0 if code in STABLES else (tickers.get(f"{code}/USDT") or {}).get("last")
            if not px:
                continue
            conv = 0.0 if code in STABLES else amount_usd * conv_bps / 1e4
            out.append((float(fee) * float(px) + conv, code, nid, float(fee)))
    return sorted(out)
