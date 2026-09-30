"""Hot-path math + host-level speed optimizers. No I/O, no exchange knowledge."""
from __future__ import annotations

import gc
import os
import sys

STABLES = frozenset({"USDT", "USDC", "FDUSD", "BUSD", "DAI", "TUSD"})


def buy_with_quote(asks, quote: float):
    """Spend `quote` walking asks -> (base_received, marginal_price) or None if depth is insufficient."""
    base, rem, last, eps = 0.0, quote, None, quote * 1e-9
    for lvl in asks:
        if rem <= eps:
            break
        p, s = lvl[0], lvl[1]
        last, cost = p, p * s
        if rem >= cost:
            base, rem = base + s, rem - cost
        else:
            base, rem = base + rem / p, 0.0
    return (base, last) if rem <= eps and last is not None else None


def walk_base(levels, base: float):
    """Consume `base` units across levels -> (quote_value, marginal_price) or None.
    Used for: selling base into bids (proceeds) and buying a fixed base from asks (cost)."""
    quote, rem, last, eps = 0.0, base, None, base * 1e-9
    for lvl in levels:
        if rem <= eps:
            break
        p, s = lvl[0], lvl[1]
        take = s if s < rem else rem
        quote, rem, last = quote + p * take, rem - take, p
    return (quote, last) if rem <= eps and last is not None else None


sell_base = walk_base
cost_for_base = walk_base


def loop_factory():
    """Fast event loop if installed (uvloop on Linux/macOS, winloop on Windows), else None (stdlib loop)."""
    try:
        if sys.platform == "win32":
            import winloop
            return winloop.new_event_loop
        import uvloop
        return uvloop.new_event_loop
    except ImportError:
        return None


def apply_speed_optimizations() -> list[str]:
    """Process-level tuning. Returns the list of optimizations that actually took effect."""
    applied = []
    if loop_factory() is not None:
        applied.append("uvloop/winloop event loop")
    # Garbage collector: freeze startup objects, make collections rare -> removes random 5-50ms pauses.
    gc.collect()
    gc.freeze()
    gc.set_threshold(200_000, 50, 50)
    applied.append("gc frozen + relaxed thresholds")
    try:
        if sys.platform == "win32":
            import ctypes
            k = ctypes.windll.kernel32
            if k.SetPriorityClass(k.GetCurrentProcess(), 0x00000080):  # HIGH_PRIORITY_CLASS
                applied.append("process priority HIGH")
        else:
            os.nice(-5)
            applied.append("process nice -5")
    except Exception:
        pass
    return applied
