from __future__ import annotations

import asyncio
import sys

from arbx.config import Config
from arbx.hub import Hub
from arbx.util import apply_speed_optimizations, loop_factory


async def _probe(cfg: Config) -> None:
    from arbx.market import LatencyGuard
    from arbx.worker import build_exchange
    print(f"{'exchange':<10} {'min':>7} {'p50':>7} {'p95':>7} {'skew':>8}  verdict")
    for x in cfg.exchanges:
        ex = None
        try:
            ex = build_exchange(x, False)
            g = LatencyGuard(ex, cfg)
            s = await g.preflight(15)
            p = s["p50"]
            v = ("EXCELLENT (co-located class)" if p < 15 else "GOOD for triangular arbitrage" if p < 40
                 else "MARGINAL - expect to lose races" if p < cfg.max_rtt_ms else "TOO FAR - paper trading only")
            print(f"{x.id:<10} {s['min']:>6.0f}ms {p:>6.0f}ms {s['p95']:>6.0f}ms {s['skew']:>+7.0f}ms  {v}")
        except Exception as e:
            print(f"{x.id:<10} ERROR {e!r}")
        finally:
            if ex is not None:
                await ex.close()


async def _transfer_plan(cfg: Config) -> None:
    from arbx.network import rank_transfer_vehicles
    from arbx.worker import build_exchange
    amount = cfg.trade_size_usd * 10
    for x in cfg.exchanges:
        ex = build_exchange(x, bool(x.api_key))
        try:
            await ex.load_markets()
            rows = await rank_transfer_vehicles(ex, amount)
            print(f"\n[{x.id}] cheapest ways to move ~${amount:.0f} (withdrawal fee + conversion):")
            for cost, code, net, fee in rows[:6]:
                print(f"  {code:<5} via {net:<10} total ~${cost:.3f}  (withdraw fee {fee} {code})")
            if not rows:
                print("  no network data (many exchanges require an API key with read permission)")
        except Exception as e:
            print(f"[{x.id}] ERROR {e!r}")
        finally:
            await ex.close()


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv and not argv[0].startswith("--") else "run"
    if cmd == "selftest":
        from arbx.selftest import run_selftest
        sys.exit(0 if run_selftest() else 1)
    cfg = Config.from_env()
    cfg.validate()
    lf = loop_factory()
    if cmd == "probe":
        asyncio.run(_probe(cfg), loop_factory=lf)
    elif cmd == "transfer-plan":
        asyncio.run(_transfer_plan(cfg), loop_factory=lf)
    elif cmd == "run":
        print("speed optimizations:", ", ".join(apply_speed_optimizations()) or "none")
        if "--headless" in argv:
            hub = Hub(cfg)
            try:
                asyncio.run(hub.run(), loop_factory=lf)
            except KeyboardInterrupt:
                print("stopped")
        else:
            from arbx.ui import run_dashboard
            run_dashboard(cfg)
    else:
        print(__doc__ or "usage: run.py [run|probe|selftest|transfer-plan] [--headless]")


if __name__ == "__main__":
    main()
