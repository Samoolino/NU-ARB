from __future__ import annotations

import asyncio
import os
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


async def _account_preflight(cfg: Config, symbol: str | None = None, *, require_live: bool = False) -> bool:
    from arbx.web_api import _make_exchange, _probe_exchange

    symbol = symbol or os.getenv("BOT_PREFLIGHT_SYMBOL", "BTC/USDT")
    ready = True
    print("READ-ONLY PREFLIGHT: no orders or transfers will be submitted")
    for x in cfg.exchanges:
        exchange = None
        credentials = {"apiKey": x.api_key} if x.api_key else {}
        if x.auth_mode in ("rsa", "ed25519"):
            if x.secret:
                credentials["privateKey"] = x.secret
        elif x.secret:
            credentials["secret"] = x.secret
        if x.password:
            credentials["password"] = x.password
        try:
            venue_id = x.venue_id or x.id
            exchange = _make_exchange(venue_id, x.auth_mode, credentials)
            evidence, balances, book = await _probe_exchange(venue_id, exchange, symbol)
            permission = evidence.get("permissions") or {}
            print(f"[{venue_id}] state={evidence['connectionState']} "
                  f"scannerEligible={str(evidence['scannerEligible']).lower()} "
                f"executionEligible={str(evidence['executionEligible']).lower()} "
                  f"liveEligible={str(evidence['liveEligible']).lower()} "
                  f"tradePermission={evidence['tradePermission']} "
                  f"withdrawalsDisabled={evidence['withdrawalsDisabled']} "
                  f"permissionSource={permission.get('source', 'unavailable')}")
            if balances:
                for asset, values in sorted(balances.items()):
                    print(f"  balance {asset}: free={values['free']:.8g} total={values['total']:.8g}")
            if book:
                print(f"  book {book['symbol']}: bid={book['bestBid']} ask={book['bestAsk']}")
            if not evidence["scannerEligible"] or (require_live and not evidence["liveEligible"]):
                ready = False
        except Exception as exc:
            print(f"[{x.id}] ERROR {type(exc).__name__}")
            ready = False
        finally:
            if exchange is not None:
                try:
                    await exchange.close()
                except Exception:
                    pass
    if ready:
        print("Live preflight passed; no orders or transfers were submitted" if require_live
              else "Preflight passed")
    else:
        print("Preflight incomplete; see per-venue evidence above")
    return ready


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
    if cmd == "hybrid-catalog":
        from arbx.hybrid.registry import VENUE_CATALOG
        from arbx.hybrid.strategies import strategy_catalog
        print("Hybrid venue catalog:", len(VENUE_CATALOG))
        for v in VENUE_CATALOG:
            print(f"  {v.id:<12} ccxt={v.ccxt_id:<12} adapter={v.adapter}")
        print("Strategies:", ", ".join(strategy_catalog()))
        return
    if cmd == "hybrid-validate":
        cfg = Config.from_env()
    if cmd == "preflight":
        cfg.mode = "paper"
    if cmd == "live-preflight":
        cfg.mode = "live"
    cfg.validate()
    lf = loop_factory()
    if cmd == "preflight":
        if not asyncio.run(_account_preflight(cfg), loop_factory=lf):
            sys.exit(1)
    elif cmd == "live-preflight":
        if not asyncio.run(_account_preflight(cfg, require_live=True), loop_factory=lf):
            sys.exit(1)
    elif cmd == "probe":
        asyncio.run(_probe(cfg), loop_factory=lf)
    elif cmd == "transfer-plan":
        asyncio.run(_transfer_plan(cfg), loop_factory=lf)
    elif cmd == "opportunities":
        from arbx.journal import TradeJournal
        journal = TradeJournal(cfg.journal_path.with_suffix(".sqlite3"))
        try:
            rows = journal.recent_opportunities()
            if not rows:
                print("No qualifying opportunities have been recorded.")
            for row in rows:
                route = row["exchange_a"] + (f" -> {row['exchange_b']}" if row["exchange_b"] else "")
                evidence = row["evidence"]
                rank = evidence.get("priorityRank")
                rank_text = f"rank={rank} " if rank is not None else ""
                available = evidence.get("availableQuoteUsd")
                funds_text = f"quote-free=${available:.4f} " if isinstance(available, (int, float)) else ""
                utilization = evidence.get("capitalUtilization")
                utilization_text = f"utilization={utilization:.1%} " if isinstance(utilization, (int, float)) else ""
                print(f"{row['timestamp']} {row['decision']:<18} {route:<22} {row['symbol']:<20} "
                      f"{rank_text}size=${row['requested_usd']:.4f} "
                      f"expected=${row['expected_net_usd']:+.4f} "
                      f"worst-case=${row['worst_case_net_usd']:+.4f} "
                      f"{funds_text}{utilization_text}age={row['book_age_ms']:.1f}ms"
                      + (f" reason={row['rejection_reason']}" if row["rejection_reason"] else ""))
        finally:
            journal.close()
    elif cmd == "run":
        print("speed optimizations:", ", ".join(apply_speed_optimizations()) or "none")
        if cfg.mode == "live" and not asyncio.run(
                _account_preflight(cfg, require_live=True), loop_factory=lf):
            sys.exit(1)
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
        print(__doc__ or "usage: run.py [run|preflight|probe|selftest|hybrid-catalog|hybrid-validate|transfer-plan] [--headless]")


if __name__ == "__main__":
    main()
