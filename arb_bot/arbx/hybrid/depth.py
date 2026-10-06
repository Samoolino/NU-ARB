from __future__ import annotations
from dataclasses import dataclass
import time
from .contracts import DepthLevel, DepthValidation, NormalizedDepth

@dataclass(frozen=True, slots=True)
class FillEstimate:
    quantity: float
    quote_cost: float
    average_price: float
    worst_price: float

def normalize_depth(venue, symbol, raw, recv=None, limit=50):
    recv = time.monotonic() if recv is None else recv
    bids=tuple(DepthLevel(float(p),float(a)) for p,a in (raw.get("bids") or [])[:limit] if float(p)>0 and float(a)>0)
    asks=tuple(DepthLevel(float(p),float(a)) for p,a in (raw.get("asks") or [])[:limit] if float(p)>0 and float(a)>0)
    return NormalizedDepth(venue,symbol,bids,asks,recv,raw.get("timestamp"),raw.get("nonce"))

def validate_depth(book, notional_usd, max_age_ms, min_levels=3):
    age=(time.monotonic()-book.received_monotonic)*1000
    levels=min(len(book.bids),len(book.asks))
    if age>max_age_ms: return DepthValidation(False,"stale_depth",levels=levels)
    if not book.bids or not book.asks: return DepthValidation(False,"empty_depth",levels=levels)
    if book.best_bid<=0 or book.best_ask<=book.best_bid: return DepthValidation(False,"invalid_top_of_book",levels=levels)
    spread=(book.best_ask/book.best_bid-1)*10000
    if levels<min_levels: return DepthValidation(False,"insufficient_depth_levels",spread_bps=spread,levels=levels)
    depth=min(sum(p*a for p,a in book.bids),sum(p*a for p,a in book.asks))
    if depth<notional_usd: return DepthValidation(False,"insufficient_visible_quote_depth",depth,book.best_bid,book.best_ask,spread,levels)
    return DepthValidation(True,"depth_validated",depth,book.best_bid,book.best_ask,spread,levels)

def walk_asks(book, quote_usd):
    remaining,base,spent,worst=quote_usd,0.0,0.0,0.0
    for l in book.asks:
        take=min(l.amount,remaining/l.price)
        if take<=0: break
        base+=take; spent+=take*l.price; remaining-=take*l.price; worst=l.price
        if remaining<=1e-12: break
    if remaining>1e-8 or base<=0: return None
    return FillEstimate(base,spent,spent/base,worst)

def walk_bids(book, base_qty):
    remaining,sold,proceeds,worst=base_qty,0.0,0.0,0.0
    for l in book.bids:
        take=min(l.amount,remaining)
        if take<=0: break
        sold+=take; proceeds+=take*l.price; remaining-=take; worst=l.price
        if remaining<=1e-12: break
    if remaining>1e-10 or sold<=0: return None
    return FillEstimate(sold,proceeds,proceeds/sold,worst)
