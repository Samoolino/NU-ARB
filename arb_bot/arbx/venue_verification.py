"""Read-only evidence report for the 20 configured CCXT venues.

This tool never accepts credentials and never invokes private endpoints or order methods.
Network probes are opt-in; reconnect behavior and key permissions remain unverified unless
separately supplied by a trusted, timestamped evidence source.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VENUES: dict[str, str] = {
    "binance": "Binance", "bybit": "Bybit", "okx": "OKX", "kucoin": "KuCoin",
    "gateio": "Gate.io", "mexc": "MEXC", "htx": "HTX", "lbank": "LBank",
    "bitget": "Bitget", "kraken": "Kraken", "coinbaseexchange": "Coinbase Exchange",
    "bitfinex": "Bitfinex", "bitstamp": "Bitstamp", "gemini": "Gemini",
    "cryptocom": "Crypto.com Exchange", "coinex": "CoinEx", "bingx": "BingX",
    "whitebit": "WhiteBIT", "bitmart": "BitMart", "upbit": "Upbit",
}
ADAPTER_ALIASES = {"gateio": "gate"}
UNVERIFIED = "unverified"


def _capability(value: Any) -> str:
    if value is True:
        return "declared"
    if value is False:
        return "not_declared"
    return UNVERIFIED


def build_empty_report() -> dict[str, Any]:
    """Create a report that never converts missing evidence into a pass."""
    try:
        ccxtpro = importlib.import_module("ccxt.pro")
        package_status = "importable"
    except Exception:
        ccxtpro = None
        package_status = "unavailable"

    rows = []
    for venue_id, name in VENUES.items():
        adapter_id = ADAPTER_ALIASES.get(venue_id, venue_id)
        cls = getattr(ccxtpro, adapter_id, None) if ccxtpro else None
        importability = "importable" if cls is not None else (
            "unavailable" if ccxtpro is None else "not_found"
        )
        has: dict[str, Any] = {}
        if cls is not None:
            try:
                description = cls.describe()
                has = description.get("has", {}) if isinstance(description, dict) else {}
            except Exception:
                has = {}
        rows.append({
            "venue_id": venue_id,
            "venue_name": name,
            "adapter_class": adapter_id,
            "adapter_importability": importability,
            "rest_capability": _capability(has.get("fetchTime") is True or has.get("fetchTicker") is True),
            "websocket_capability": _capability(has.get("watchOrderBook") is True),
            "public_connectivity": UNVERIFIED,
            "data_freshness": UNVERIFIED,
            "reconnect_behavior": UNVERIFIED,
            "permission_evidence": UNVERIFIED,
            "evidence": [],
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ccxt_pro_package": package_status,
        "mode": "metadata_only",
        "safety_boundary": "No credentials, private endpoints, orders, signing, withdrawals, or transfers.",
        "venues": rows,
        "summary": {
            "catalogue_count": len(rows),
            "public_connectivity_verified": 0,
            "freshness_verified": 0,
            "reconnect_verified": 0,
            "permission_verified": 0,
        },
    }


async def probe_public_connectivity(report: dict[str, Any], *, timeout_seconds: float = 5.0) -> None:
    """Probe public metadata only. Results are runtime observations, not permission evidence."""
    try:
        ccxtpro = importlib.import_module("ccxt.pro")
    except Exception:
        return
    for row in report["venues"]:
        cls = getattr(ccxtpro, row["adapter_class"], None)
        if cls is None:
            row["public_connectivity"] = "unavailable"
            continue
        exchange = None
        started = datetime.now(timezone.utc)
        try:
            exchange = cls({"enableRateLimit": True, "timeout": int(timeout_seconds * 1000)})
            has = getattr(exchange, "has", {}) or {}
            if has.get("fetchTime") is True:
                await asyncio.wait_for(exchange.fetch_time(), timeout=timeout_seconds)
            else:
                # load_markets is public metadata; it does not request account state.
                await asyncio.wait_for(exchange.load_markets(), timeout=timeout_seconds)
            finished = datetime.now(timezone.utc)
            row["public_connectivity"] = "passed"
            row["data_freshness"] = "observed_now"
            row["evidence"].append({
                "kind": "public_connectivity",
                "method": "fetch_time" if has.get("fetchTime") is True else "load_markets",
                "observed_at": started.isoformat(),
                "retrieved_at": finished.isoformat(),
                "latency_ms": round((finished - started).total_seconds() * 1000, 2),
            })
        except asyncio.TimeoutError:
            row["public_connectivity"] = "timeout"
        except Exception as exc:
            row["public_connectivity"] = "failed"
            row["evidence"].append({"kind": "public_connectivity", "error_code": type(exc).__name__})
        finally:
            if exchange is not None:
                try:
                    await exchange.close()
                except Exception:
                    pass
    report["mode"] = "public_read_only_probe"
    report["summary"]["public_connectivity_verified"] = sum(
        row["public_connectivity"] == "passed" for row in report["venues"]
    )
    report["summary"]["freshness_verified"] = sum(
        row["data_freshness"] == "observed_now" for row in report["venues"]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-network", action="store_true",
                        help="perform bounded public metadata requests; no credentials are used")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=Path("venue-verification-report.json"))
    args = parser.parse_args()
    if args.timeout <= 0 or args.timeout > 30:
        parser.error("--timeout must be greater than 0 and at most 30 seconds")
    report = build_empty_report()
    if args.public_network:
        asyncio.run(probe_public_connectivity(report, timeout_seconds=args.timeout))
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
