from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from arbx.config import Config
from arbx.hub import Hub
from arbx.util import apply_speed_optimizations, loop_factory


PUBLIC_FEED_VENUES = ("binance", "mexc", "kucoin", "htx")
REQUIRED_LIVE_VENUES = ("binance", "mexc", "kucoin", "htx")


async def _check_public_feed(venue_id: str, symbol: str, ccxt_id: str | None = None) -> dict:
    from arbx.hybrid.adapters import CCXTProAdapter, safe_ccxt_error

    adapter = CCXTProAdapter(venue_id, ccxt_id=ccxt_id, credentials={})
    result = {
        "venue": venue_id,
        "symbol": symbol,
        "authenticated": False,
        "ordersSubmitted": False,
        "restOk": False,
        "websocketOk": False,
    }
    stage = "market_discovery"
    try:
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(adapter.connect(), timeout=25)
        result["marketLoadMs"] = round((asyncio.get_running_loop().time() - started) * 1000, 1)
        market = await adapter.get_market(symbol)
        if not market or market.get("spot") is not True or market.get("contract") is True:
            result["reason"] = "spot_market_unavailable"
            return result

        stage = "rest_orderbook"
        started = asyncio.get_running_loop().time()
        rest_book = await asyncio.wait_for(adapter.get_order_book(symbol, 5), timeout=20)
        result["restRttMs"] = round((asyncio.get_running_loop().time() - started) * 1000, 1)
        result["restOk"] = bool(rest_book.get("bids") and rest_book.get("asks"))
        result["restLevels"] = {
            "bids": len(rest_book.get("bids") or []),
            "asks": len(rest_book.get("asks") or []),
        }

        stage = "websocket_orderbook"
        started = asyncio.get_running_loop().time()
        ws_limit = 50 if venue_id == "bybit" else 5
        ws_result = await asyncio.wait_for(
            adapter.verify_public_stream(symbol, ws_limit), timeout=25
        )
        result["websocketFirstBookMs"] = round(
            (asyncio.get_running_loop().time() - started) * 1000, 1
        )
        ws_book = ws_result.get("book") or {}
        result["websocketOk"] = bool(
            ws_result.get("ok") and ws_book.get("bids") and ws_book.get("asks")
        )
        result["websocketLevels"] = {
            "bids": len(ws_book.get("bids") or []),
            "asks": len(ws_book.get("asks") or []),
        }
        result["exchangeTimestampMs"] = ws_book.get("timestamp")
        result["sequence"] = ws_book.get("nonce")
        result["verified"] = result["restOk"] and result["websocketOk"]
        if not result["verified"]:
            result["reason"] = "order_book_missing_one_or_both_sides"
    except Exception as exc:
        result["failedStage"] = stage
        result["reason"] = safe_ccxt_error(exc, adapter.ex, adapter.credentials)
    finally:
        try:
            await adapter.close()
        except Exception:
            pass
    return result


async def _public_feed_check(venues: tuple[str, ...], symbol: str) -> bool:
    results = await asyncio.gather(
        *(_check_public_feed(venue_id, symbol) for venue_id in venues)
    )
    for result in results:
        print(
            f"[{result['venue']}] REST_BOOK={str(result['restOk']).lower()} "
            f"WS_BOOK={str(result['websocketOk']).lower()} "
            f"VERIFIED={str(result.get('verified', False)).lower()}"
        )
        if result.get("restRttMs") is not None:
            print(f"  REST RTT={result['restRttMs']} ms")
        if result.get("websocketFirstBookMs") is not None:
            print(f"  WS first book={result['websocketFirstBookMs']} ms")
        if result.get("reason"):
            if result.get("failedStage"):
                print(f"  failed stage={result['failedStage']}")
            print(f"  reason={result['reason']}")
    ready = all(result.get("verified") is True for result in results)
    print(
        "All public spot feeds verified; no authentication or orders used."
        if ready
        else "Public feed verification incomplete; trading remains disabled."
    )
    return ready


async def _live_engagement_audit(symbol: str, report_dir: Path) -> bool:
    from arbx.hybrid.networks import MAJOR_NETWORKS
    from arbx.hybrid.registry import VENUE_CATALOG
    from arbx.hybrid.strategies import strategy_catalog

    semaphore = asyncio.Semaphore(3)

    async def check_venue(spec):
        async with semaphore:
            result = await _check_public_feed(spec.id, symbol, spec.ccxt_id)
        result["catalogAdapter"] = spec.adapter
        result["authenticatedBalanceVerified"] = False
        result["privateStreamVerified"] = False
        result["permissionsVerified"] = False
        result["executionRouteVerified"] = False
        result["liveEngageable"] = False
        result["blockers"] = [
            "authenticated_account_not_checked",
            "private_user_stream_not_checked",
            "trading_permissions_not_checked",
            "execution_route_not_checked",
        ]
        if not result.get("verified"):
            result["blockers"].append("public_rest_and_websocket_not_both_verified")
        return result

    venue_results = await asyncio.gather(*(check_venue(spec) for spec in VENUE_CATALOG))
    generated_at = datetime.now(timezone.utc).isoformat()
    networks = [
        {"id": item.id, "family": item.family, "evidence": "catalog_only"}
        for item in MAJOR_NETWORKS
    ]
    strategies = [
        {"id": name, "cataloged": True, "live_execution_verified": False}
        for name in strategy_catalog()
    ]
    report = {
        "schemaVersion": 1,
        "generatedAtUtc": generated_at,
        "symbol": symbol,
        "scope": "read_only_public_market_data",
        "authenticated": False,
        "ordersSubmitted": False,
        "withdrawalsOrTransfersSubmitted": False,
        "liveEngageable": False,
        "liveModeState": "BLOCKED_NOT_AUTHENTICATED",
        "priceDifferenceProfitAssurance": False,
        "persistenceValidation": {
            "validated": False,
            "method": "atomic_write_then_json_readback",
        },
        "limitations": [
            "A modeled price difference is not an assurance of executable or realized profit.",
            "Venue catalog membership does not verify account permissions, balances, private streams, or order execution.",
            "Network catalog entries are not evidence that every venue supports a chain or that a transfer route is available.",
            "The audit does not place orders, perform transfers, authenticate, or enable live mode.",
        ],
        "venues": venue_results,
        "settlementNetworks": networks,
        "strategies": strategies,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamped = report_dir / f"live-engagement-audit-{stamp}.json"
    latest = report_dir / "live-engagement-audit-latest.json"
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    report["persistenceValidation"]["artifactPaths"] = [
        str(timestamped),
        str(latest),
    ]
    report["persistenceValidation"]["validated"] = True
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    for artifact in (timestamped, latest):
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=report_dir,
                prefix=f".{artifact.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temp_path = Path(output.name)
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, artifact)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    for artifact in (timestamped, latest):
        persisted = json.loads(artifact.read_text(encoding="utf-8"))
        if (
            persisted.get("schemaVersion") != 1
            or persisted.get("generatedAtUtc") != generated_at
            or persisted.get("persistenceValidation", {}).get("validated") is not True
            or len(persisted.get("venues", [])) != len(VENUE_CATALOG)
        ):
            raise RuntimeError(f"persisted engagement audit failed read-back validation: {artifact}")

    verified_feeds = sum(item.get("verified") is True for item in venue_results)
    print(f"Public REST + WebSocket feeds verified: {verified_feeds}/{len(venue_results)}")
    print(f"Cataloged settlement networks (not venue-route verified): {len(networks)}")
    print(f"Cataloged strategies (not live-execution verified): {len(strategies)}")
    print("LIVE_ENGAGEABLE=false; no authenticated accounts or order routes were tested.")
    print(f"Persistence read-back validated: {timestamped} ; {latest}")
    return verified_feeds == len(venue_results)


async def _required_live_venue_audit(symbol: str, report_dir: Path) -> bool:
    import ccxt.pro as ccxtpro

    from arbx.hybrid.registry import VENUE_CATALOG

    catalog = {spec.id: spec for spec in VENUE_CATALOG}
    specs = [catalog[venue_id] for venue_id in REQUIRED_LIVE_VENUES]
    semaphore = asyncio.Semaphore(3)

    async def check_required_venue(spec):
        ccxt_constructor = getattr(ccxtpro, spec.ccxt_id, None)
        constructor_available = callable(ccxt_constructor)
        if constructor_available:
            async with semaphore:
                result = await _check_public_feed(spec.id, symbol, spec.ccxt_id)
        else:
            result = {
                "venue": spec.id,
                "symbol": symbol,
                "authenticated": False,
                "ordersSubmitted": False,
                "restOk": False,
                "websocketOk": False,
                "verified": False,
                "failedStage": "adapter_resolution",
                "reason": f"CCXT Pro constructor unavailable: {spec.ccxt_id}",
            }

        blockers = []
        if not constructor_available:
            blockers.append("ccxt_pro_adapter_constructor_unavailable")
        if result.get("restOk") is not True:
            blockers.append("public_rest_spot_orderbook_not_verified")
        if result.get("websocketOk") is not True:
            blockers.append("public_websocket_spot_orderbook_not_verified")
        if result.get("reason"):
            blockers.append(
                f"feed_failure:{result.get('failedStage', 'verification')}:{result['reason']}"
            )

        blockers.extend((
            "authenticated_credentials_and_account_scope_not_verified",
            "available_balances_and_reservations_not_verified",
            "private_balance_or_order_stream_not_verified",
            "spot_permissions_and_withdrawal_restrictions_not_verified",
            "ioc_order_route_minimums_and_fee_tier_not_verified",
            "cross_venue_asset_identity_and_settlement_route_not_verified",
        ))
        result.update({
            "adapter": "ccxt_pro",
            "ccxtId": spec.ccxt_id,
            "adapterConstructorAvailable": constructor_available,
            "authenticatedAccountVerified": False,
            "balancesVerified": False,
            "privateStreamVerified": False,
            "permissionsVerified": False,
            "liveExecutionRouteVerified": False,
            "liveEngageable": False,
            "blockers": blockers,
        })
        return result

    venue_results = await asyncio.gather(
        *(check_required_venue(spec) for spec in specs)
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    report = {
        "schemaVersion": 1,
        "auditType": "required_live_venue_readiness",
        "generatedAtUtc": generated_at,
        "symbol": symbol,
        "requiredVenues": list(REQUIRED_LIVE_VENUES),
        "authenticated": False,
        "ordersSubmitted": False,
        "withdrawalsOrTransfersSubmitted": False,
        "liveModeState": "BLOCKED_REQUIRED_EVIDENCE_MISSING",
        "liveEngageable": False,
        "priceDifferenceProfitAssurance": False,
        "clarification": (
            "A public REST/WebSocket order book only verifies public market-data access. "
            "It does not verify authenticated balances, private streams, account permissions, "
            "fees/order constraints, asset identity, settlement routes, or execution. "
            "A price difference is not an assurance of executable or realized profit."
        ),
        "persistenceValidation": {
            "validated": False,
            "method": "atomic_write_then_json_readback",
        },
        "venues": venue_results,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamped = report_dir / f"required-live-venue-audit-{stamp}.json"
    latest = report_dir / "required-live-venue-audit-latest.json"
    report["persistenceValidation"]["artifactPaths"] = [str(timestamped), str(latest)]
    report["persistenceValidation"]["validated"] = True
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    for artifact in (timestamped, latest):
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=report_dir,
                prefix=f".{artifact.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temp_path = Path(output.name)
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, artifact)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    for artifact in (timestamped, latest):
        persisted = json.loads(artifact.read_text(encoding="utf-8"))
        if (
            persisted.get("schemaVersion") != 1
            or persisted.get("auditType") != "required_live_venue_readiness"
            or persisted.get("generatedAtUtc") != generated_at
            or persisted.get("persistenceValidation", {}).get("validated") is not True
            or [item.get("venue") for item in persisted.get("venues", [])]
            != list(REQUIRED_LIVE_VENUES)
        ):
            raise RuntimeError(
                f"persisted required-venue audit failed read-back validation: {artifact}"
            )

    for result in venue_results:
        print(
            f"[{result['venue']}] adapter={result['adapter']} "
            f"constructor={str(result['adapterConstructorAvailable']).lower()} "
            f"REST={str(result['restOk']).lower()} WS={str(result['websocketOk']).lower()} "
            "LIVE_ENGAGEABLE=false"
        )
        if result.get("reason"):
            print(f"  feed failure: {result.get('failedStage')}: {result['reason']}")
        print("  live blockers: " + ", ".join(result["blockers"]))
    feed_ready = all(
        result.get("adapterConstructorAvailable") is True
        and result.get("verified") is True
        for result in venue_results
    )
    print(f"Required public feeds complete: {sum(bool(r.get('verified')) for r in venue_results)}/4")
    print(f"Persistent clarification report read-back validated: {latest}")
    print("LIVE_ENGAGEABLE=false; credentials and trading mode were not enabled.")
    return feed_ready


async def _random_pair_venue_check(spec, rng: random.Random, semaphore: asyncio.Semaphore) -> dict:
    import ccxt.pro as ccxtpro

    from arbx.hybrid.adapters import CCXTProAdapter, safe_ccxt_error

    result = {
        "venue": spec.id,
        "adapter": spec.adapter,
        "ccxtId": spec.ccxt_id,
        "adapterConstructorAvailable": callable(getattr(ccxtpro, spec.ccxt_id, None)),
        "authenticated": False,
        "ordersSubmitted": False,
        "restOk": False,
        "websocketOk": False,
        "verified": False,
    }
    if not result["adapterConstructorAvailable"]:
        result.update({
            "failedStage": "adapter_resolution",
            "reason": f"CCXT Pro adapter unavailable: {spec.ccxt_id}",
        })
        return result

    adapter = CCXTProAdapter(spec.id, ccxt_id=spec.ccxt_id, credentials={})
    stage = "market_discovery"
    try:
        async with semaphore:
            started = asyncio.get_running_loop().time()
            await asyncio.wait_for(adapter.connect(), timeout=20)
            result["marketLoadMs"] = round(
                (asyncio.get_running_loop().time() - started) * 1000, 1
            )

            markets = list((adapter.ex.markets or {}).values())
            eligible = [
                market for market in markets
                if market.get("spot") is True
                and market.get("contract") is not True
                and market.get("active") is not False
                and isinstance(market.get("symbol"), str)
                and "/" in market["symbol"]
            ]
            stable_quotes = {"USDT", "USDC", "USD", "DAI"}
            preferred = [
                market for market in eligible
                if market.get("quote") in stable_quotes
            ]
            pool = preferred or eligible
            result["spotMarketCount"] = len(eligible)
            result["samplePoolCount"] = len(pool)
            if not pool:
                result["failedStage"] = "spot_market_selection"
                result["reason"] = "no_active_spot_pair_available_for_sampling"
                return result

            market = rng.choice(pool)
            symbol = market["symbol"]
            result["symbol"] = symbol
            result["marketMetadata"] = {
                "spot": market.get("spot"),
                "contract": market.get("contract"),
                "active": market.get("active"),
                "base": market.get("base"),
                "quote": market.get("quote"),
            }

            stage = "rest_orderbook"
            rest_limit = 25 if spec.id == "bitfinex" else 5
            started = asyncio.get_running_loop().time()
            rest_book = await asyncio.wait_for(
                adapter.get_order_book(symbol, rest_limit), timeout=15
            )
            result["restRttMs"] = round(
                (asyncio.get_running_loop().time() - started) * 1000, 1
            )
            result["restOk"] = bool(rest_book.get("bids") and rest_book.get("asks"))
            result["restLevels"] = {
                "bids": len(rest_book.get("bids") or []),
                "asks": len(rest_book.get("asks") or []),
            }

            stage = "websocket_orderbook"
            ws_limit = 50 if spec.id == "bybit" else 25 if spec.id == "bitfinex" else 5
            started = asyncio.get_running_loop().time()
            ws_result = await asyncio.wait_for(
                adapter.verify_public_stream(symbol, ws_limit), timeout=20
            )
            result["websocketFirstBookMs"] = round(
                (asyncio.get_running_loop().time() - started) * 1000, 1
            )
            ws_book = ws_result.get("book") or {}
            result["websocketOk"] = bool(
                ws_result.get("ok") and ws_book.get("bids") and ws_book.get("asks")
            )
            result["websocketLevels"] = {
                "bids": len(ws_book.get("bids") or []),
                "asks": len(ws_book.get("asks") or []),
            }
            result["exchangeTimestampMs"] = ws_book.get("timestamp")
            result["sequence"] = ws_book.get("nonce")
            rest_depth = _depth_pilot_metrics(spec.id, symbol, rest_book)
            websocket_depth = _depth_pilot_metrics(spec.id, symbol, ws_book)
            result["depthPilot"] = {
                "notionalQuote": 25.0,
                "quoteCurrency": market.get("quote"),
                "rest": rest_depth,
                "websocket": websocket_depth,
                "passed": all(
                    source["depthValidated"]
                    and source["simulatedImmediateRoundTrip"]["completed"]
                    for source in (rest_depth, websocket_depth)
                ),
                "profitAssurance": False,
                "chainSettlementVerified": False,
                "ordersSubmitted": False,
            }
            result["verified"] = result["restOk"] and result["websocketOk"]
            if not result["verified"]:
                result["failedStage"] = "book_validation"
                result["reason"] = "REST_and_WebSocket_must_both_have_bid_and_ask_depth"
    except Exception as exc:
        result["failedStage"] = stage
        result["reason"] = safe_ccxt_error(exc, adapter.ex, adapter.credentials)
    finally:
        try:
            await adapter.close()
        except Exception:
            pass
    return result


def _depth_pilot_metrics(venue_id: str, symbol: str, book: dict, notional_quote: float = 25.0) -> dict:
    from arbx.hybrid.depth import normalize_depth, validate_depth, walk_asks, walk_bids

    normalized = normalize_depth(venue_id, symbol, book, limit=50)
    validation = validate_depth(
        normalized,
        notional_quote,
        max_age_ms=5_000,
        min_levels=3,
    )
    buy = walk_asks(normalized, notional_quote)
    sell = walk_bids(normalized, buy.quantity) if buy is not None else None
    round_trip = {
        "completed": buy is not None and sell is not None,
        "grossPnlQuote": None,
        "grossPnlBps": None,
        "includesFees": False,
    }
    if buy is not None and sell is not None:
        gross_pnl = sell.quote_cost - buy.quote_cost
        round_trip.update({
            "grossPnlQuote": round(gross_pnl, 12),
            "grossPnlBps": round(gross_pnl / buy.quote_cost * 10_000, 4),
        })
    return {
        "depthValidated": validation.ok,
        "reason": validation.reason,
        "visibleDepthQuote": round(validation.depth_usd, 12),
        "levelsPerSide": validation.levels,
        "bestBid": validation.top_bid,
        "bestAsk": validation.top_ask,
        "spreadBps": round(validation.spread_bps, 4),
        "simulatedImmediateRoundTrip": round_trip,
    }


async def _randomized_venue_feed_audit(report_dir: Path) -> bool:
    from arbx.hybrid.registry import VENUE_CATALOG

    sample_seed = secrets.randbits(64)
    semaphore = asyncio.Semaphore(4)
    venue_seeds = {
        spec.id: int.from_bytes(
            hashlib.sha256(f"{sample_seed}:{spec.id}".encode("utf-8")).digest()[:8],
            "big",
        )
        for spec in VENUE_CATALOG
    }
    results = await asyncio.gather(*(
        _random_pair_venue_check(spec, random.Random(venue_seeds[spec.id]), semaphore)
        for spec in VENUE_CATALOG
    ))

    required_live_evidence = (
        "authenticated_account_scope_not_verified",
        "available_balance_and_order_reservations_not_verified",
        "private_user_stream_not_verified",
        "spot_trade_permission_and_withdrawal_scope_not_verified",
        "ioc_route_market_minimums_and_actual_fee_tier_not_verified",
        "asset_identity_and_settlement_network_route_not_verified",
        "risk_gate_not_evaluated_with_authenticated_state",
    )
    for result in results:
        blockers = list(required_live_evidence)
        if not result.get("adapterConstructorAvailable"):
            blockers.insert(0, "configured_ccxt_pro_adapter_unavailable")
        if result.get("restOk") is not True:
            blockers.insert(0, "random_pair_public_rest_orderbook_not_verified")
        if result.get("websocketOk") is not True:
            blockers.insert(0, "random_pair_public_websocket_orderbook_not_verified")
        if result.get("reason"):
            blockers.insert(
                0,
                f"feed_failure:{result.get('failedStage', 'unknown')}:{result['reason']}",
            )
        depth_pilot = result.get("depthPilot")
        if not isinstance(depth_pilot, dict) or depth_pilot.get("passed") is not True:
            blockers.insert(0, "public_depth_strategy_pilot_not_validated")
        result.update({
            "authenticatedAccountVerified": False,
            "balancesVerified": False,
            "privateStreamVerified": False,
            "permissionsVerified": False,
            "liveExecutionRouteVerified": False,
            "liveEngageable": False,
            "liveBlockers": blockers,
        })

    generated_at = datetime.now(timezone.utc).isoformat()
    report = {
        "schemaVersion": 1,
        "auditType": "randomized_all_venue_public_feed_audit",
        "generatedAtUtc": generated_at,
        "selection": {
            "method": "random active spot pair, preferring USD stablecoin quotes",
            "seed": sample_seed,
            "venueSeeds": venue_seeds,
            "onePairPerVenue": True,
        },
        "depthPilot": {
            "notionalQuote": 25.0,
            "minimumLevelsPerSide": 3,
            "sources": ["rest", "websocket"],
            "scope": "single-venue-book-depth-and-immediate-round-trip-simulation",
            "chainSettlementVerified": False,
            "profitAssurance": False,
            "ordersSubmitted": False,
        },
        "scope": "unauthenticated_public_spot_market_data_only",
        "authenticated": False,
        "ordersSubmitted": False,
        "withdrawalsOrTransfersSubmitted": False,
        "liveEngageable": False,
        "liveModeState": "BLOCKED_REQUIRED_EVIDENCE_MISSING",
        "priceDifferenceProfitAssurance": False,
        "clarification": (
            "This audit samples one current listed spot pair per venue and tests an actual "
            "REST order-book snapshot and WebSocket order-book event on that same pair. "
            "Its depth pilot validates visible levels and simulates a fixed 25-unit quote "
            "round trip within each individual book; it is not cross-venue execution, "
            "chain-settlement verification, or profit assurance. "
            "It does not establish account authorization, available balances, private stream "
            "health, fee tier, executable cross-venue depth, matched asset identity, settlement "
            "routes, execution success, or guaranteed profit."
        ),
        "persistenceValidation": {
            "validated": False,
            "method": "atomic_write_then_json_readback",
        },
        "venues": results,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamped = report_dir / f"randomized-venue-feed-audit-{stamp}.json"
    latest = report_dir / "randomized-venue-feed-audit-latest.json"
    report["persistenceValidation"]["artifactPaths"] = [str(timestamped), str(latest)]
    report["persistenceValidation"]["validated"] = True
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    for artifact in (timestamped, latest):
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=report_dir,
                prefix=f".{artifact.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temp_path = Path(output.name)
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, artifact)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    expected_venues = [spec.id for spec in VENUE_CATALOG]
    for artifact in (timestamped, latest):
        persisted = json.loads(artifact.read_text(encoding="utf-8"))
        if (
            persisted.get("schemaVersion") != 1
            or persisted.get("auditType") != "randomized_all_venue_public_feed_audit"
            or persisted.get("generatedAtUtc") != generated_at
            or persisted.get("selection", {}).get("seed") != sample_seed
            or persisted.get("selection", {}).get("venueSeeds") != venue_seeds
            or persisted.get("depthPilot") != report["depthPilot"]
            or persisted.get("persistenceValidation", {}).get("validated") is not True
            or [venue.get("venue") for venue in persisted.get("venues", [])] != expected_venues
            or persisted.get("venues") != results
        ):
            raise RuntimeError(f"persisted randomized venue audit failed validation: {artifact}")

    verified_count = sum(result.get("verified") is True for result in results)
    depth_pass_count = sum(
        isinstance(result.get("depthPilot"), dict)
        and result["depthPilot"].get("passed") is True
        for result in results
    )
    for result in results:
        depth_pilot = result.get("depthPilot")
        print(
            f"[{result['venue']}] adapter={result['adapter']} "
            f"pair={result.get('symbol', 'unavailable')} "
            f"REST={str(result['restOk']).lower()} WS={str(result['websocketOk']).lower()} "
            f"DEPTH_PILOT={str(isinstance(depth_pilot, dict) and depth_pilot.get('passed') is True).lower()} "
            f"LIVE_ENGAGEABLE=false"
        )
        if result.get("reason"):
            print(f"  failed {result.get('failedStage')}: {result['reason']}")
    print(f"Random-pair REST+WebSocket checks passed: {verified_count}/{len(results)}")
    print(f"Random-pair REST+WebSocket depth pilot passed: {depth_pass_count}/{len(results)}")
    print(f"Persistent random-pair audit validated: {latest}")
    print("Live engagement remains blocked; no authenticated checks or orders were performed.")
    return verified_count == len(results) and depth_pass_count == len(results)


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
            if x.private_key:
                credentials["privateKey"] = x.private_key
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


async def _permission_revalidation(venues: tuple[str, ...], report_dir: Path) -> dict:
    from arbx.hybrid.adapters import CCXTProAdapter
    from arbx.permission_revalidation import revalidate_permissions_until_resolved
    from arbx.permissions import inspect_permissions

    env_values = {}
    adapters = {}
    for venue_id in venues:
        prefix = f"BOT_{venue_id.upper()}"
        auth_mode = os.getenv(f"{prefix}_AUTH_MODE", "hmac").strip().lower()
        credentials = {}
        api_key = os.getenv(f"{prefix}_KEY", "")
        if api_key:
            credentials["apiKey"] = api_key
        if auth_mode == "hmac":
            secret = os.getenv(f"{prefix}_SECRET", "")
            if secret:
                credentials["secret"] = secret
        elif auth_mode in ("rsa", "ed25519"):
            private_key = os.getenv(f"{prefix}_PRIVATE_KEY", "")
            if private_key:
                credentials["privateKey"] = private_key
        password = (
            os.getenv(f"{prefix}_PASSWORD")
            or os.getenv(f"{prefix}_PASSPHRASE")
            or ""
        )
        if password:
            credentials["password"] = password
        if not credentials.get("apiKey") or not (
            credentials.get("secret") or credentials.get("privateKey")
        ):
            raise ValueError(
                f"Missing local credentials for {venue_id}; configure BOT_{venue_id.upper()}_KEY "
                f"and BOT_{venue_id.upper()}_SECRET (plus BOT_{venue_id.upper()}_PASSWORD "
                "where required). Credential values are never saved by this command."
            )
        env_values[venue_id] = (auth_mode, credentials)

    async def probe(venue_id: str) -> dict:
        auth_mode, credentials = env_values[venue_id]
        adapter = CCXTProAdapter(
            venue_id,
            credentials=credentials,
            auth_mode=auth_mode,
        )
        try:
            await asyncio.wait_for(adapter.connect(), timeout=30)
            return await asyncio.wait_for(
                inspect_permissions(venue_id, adapter.ex),
                timeout=20,
            )
        finally:
            try:
                await adapter.close()
            except Exception:
                pass

    base_delay = float(os.getenv("ARBX_PERMISSION_RETRY_BASE_SECONDS", "30"))
    max_delay = float(os.getenv("ARBX_PERMISSION_RETRY_MAX_SECONDS", "900"))

    def print_update(venue_id: str, evidence: dict) -> None:
        print(
            f"[{venue_id}] ATTEMPT={evidence['attempts']} "
            f"STATE={evidence['state']} "
            f"VALIDATED={str(evidence.get('validationComplete', False)).lower()} "
            f"LIVE_ELIGIBLE={str(evidence.get('liveEligible', False)).lower()}"
        )
        if evidence.get("nextRetrySeconds") is not None:
            print(f"  retrying after {evidence['nextRetrySeconds']} seconds")
        if evidence.get("policyBlockers"):
            print("  blockers: " + ", ".join(evidence["policyBlockers"]))
        if evidence.get("lastFailureType"):
            print("  last error class: " + evidence["lastFailureType"])

    return await revalidate_permissions_until_resolved(
        venues,
        probe,
        report_dir,
        base_retry_seconds=base_delay,
        max_retry_seconds=max_delay,
        on_update=print_update,
    )


async def _authenticated_feed_validation(
    venues: tuple[str, ...], symbol: str, report_dir: Path
) -> dict:
    from arbx.authenticated_feed_validation import (
        summarize_strict_book,
        validate_authenticated_feeds_until_resolved,
        validate_pair_format,
    )
    from arbx.hybrid.adapters import CCXTProAdapter
    from arbx.permissions import inspect_permissions

    symbol = validate_pair_format(symbol)
    env_values = {}
    for venue_id in venues:
        prefix = f"BOT_{venue_id.upper()}"
        auth_mode = os.getenv(f"{prefix}_AUTH_MODE", "hmac").strip().lower()
        credentials = {}
        api_key = os.getenv(f"{prefix}_KEY", "")
        if api_key:
            credentials["apiKey"] = api_key
        if auth_mode == "hmac":
            secret = os.getenv(f"{prefix}_SECRET", "")
            if secret:
                credentials["secret"] = secret
        elif auth_mode in ("rsa", "ed25519"):
            private_key = os.getenv(f"{prefix}_PRIVATE_KEY", "")
            if private_key:
                credentials["privateKey"] = private_key
        password = os.getenv(f"{prefix}_PASSWORD") or os.getenv(f"{prefix}_PASSPHRASE") or ""
        if password:
            credentials["password"] = password
        if not credentials.get("apiKey") or not (
            credentials.get("secret") or credentials.get("privateKey")
        ):
            raise ValueError(
                f"Missing local credentials for {venue_id}; use the masked "
                "validate-authenticated-feeds.ps1 launcher."
            )
        env_values[venue_id] = (auth_mode, credentials)

    async def probe(venue_id: str, pair: str) -> dict:
        auth_mode, credentials = env_values[venue_id]
        adapter = CCXTProAdapter(
            venue_id,
            credentials=credentials,
            auth_mode=auth_mode,
        )
        stage = "connect_and_load_markets"
        try:
            await asyncio.wait_for(adapter.connect(), timeout=30)

            stage = "authenticated_permission_probe"
            permissions = await asyncio.wait_for(
                inspect_permissions(venue_id, adapter.ex),
                timeout=20,
            )
            result = {
                "permissionValidated": permissions.get("validationComplete") is True,
                "permissions": permissions,
                "market": {},
                "rest": {},
                "websocket": {},
                "restBookValid": False,
                "websocketBookValid": False,
                "blockers": [],
            }
            if not result["permissionValidated"]:
                result["blockers"].extend(permissions.get("policyBlockers") or ())
                result["blockers"].append("permission_evidence_not_validated")
                return result

            stage = "strict_spot_pair_check"
            market = await adapter.get_market(pair)
            result["market"] = {
                "symbol": pair,
                "available": bool(market),
                "spot": market.get("spot") is True if market else False,
                "contract": market.get("contract") is True if market else False,
                "active": market.get("active") if market else None,
            }
            if not market or market.get("spot") is not True or market.get("contract") is True:
                result["blockers"].append("strict_pair_not_available_as_spot_market")
                return result
            if market.get("active") is False:
                result["blockers"].append("strict_pair_market_inactive")
                return result

            stage = "rest_orderbook"
            started = asyncio.get_running_loop().time()
            rest_book = await asyncio.wait_for(adapter.get_order_book(pair, 10), timeout=20)
            result["rest"]["rttMs"] = round(
                (asyncio.get_running_loop().time() - started) * 1000, 1
            )
            rest_summary = summarize_strict_book(rest_book)
            result["rest"].update(rest_summary)
            result["restBookValid"] = rest_summary.get("ok") is True

            stage = "websocket_orderbook"
            started = asyncio.get_running_loop().time()
            ws_result = await asyncio.wait_for(
                adapter.verify_public_stream(pair, 10),
                timeout=25,
            )
            result["websocket"]["firstBookMs"] = round(
                (asyncio.get_running_loop().time() - started) * 1000, 1
            )
            ws_book = ws_result.get("book") if isinstance(ws_result, dict) else None
            ws_summary = summarize_strict_book(ws_book)
            result["websocket"].update(ws_summary)
            result["websocketBookValid"] = (
                ws_result.get("ok") is True and ws_summary.get("ok") is True
            )
            if not result["restBookValid"]:
                result["blockers"].append(
                    "rest_" + result["rest"].get("reason", "order_book_invalid")
                )
            if not result["websocketBookValid"]:
                result["blockers"].append(
                    "websocket_" + result["websocket"].get("reason", "order_book_invalid")
                )
            return result
        except Exception as exc:
            try:
                exc.validation_stage = stage
            except Exception:
                pass
            raise
        finally:
            try:
                await adapter.close()
            except Exception:
                pass

    base_delay = float(os.getenv("ARBX_FEED_RETRY_BASE_SECONDS", "30"))
    max_delay = float(os.getenv("ARBX_FEED_RETRY_MAX_SECONDS", "900"))

    def print_update(venue_id: str, evidence: dict) -> None:
        print(
            f"[{venue_id}] pair={symbol} attempt={evidence['attempts']} "
            f"state={evidence['state']} "
            f"permissions={str(evidence.get('permissionValidated', False)).lower()} "
            f"REST={str(evidence.get('restBookValid', False)).lower()} "
            f"WS={str(evidence.get('websocketBookValid', False)).lower()}"
        )
        if evidence.get("failedStage"):
            print(f"  failed stage={evidence['failedStage']}")
        if evidence.get("nextRetrySeconds") is not None:
            print(f"  retrying after {evidence['nextRetrySeconds']} seconds")
        if evidence.get("blockers"):
            print("  blockers: " + ", ".join(evidence["blockers"]))

    return await validate_authenticated_feeds_until_resolved(
        venues,
        symbol,
        probe,
        report_dir,
        base_retry_seconds=base_delay,
        max_retry_seconds=max_delay,
        on_update=print_update,
    )


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv and not argv[0].startswith("--") else "run"
    if cmd == "selftest":
        from arbx.selftest import run_selftest
        sys.exit(0 if run_selftest() else 1)
    if cmd == "public-feed-check":
        venues = tuple(argv[1:]) or PUBLIC_FEED_VENUES
        invalid = [venue for venue in venues if venue not in PUBLIC_FEED_VENUES]
        if invalid:
            print("unsupported public-feed venue(s): " + ", ".join(invalid))
            sys.exit(2)
        symbol = os.getenv("BOT_PREFLIGHT_SYMBOL", "BTC/USDT")
        if not symbol or "/" not in symbol:
            print("BOT_PREFLIGHT_SYMBOL must be a canonical BASE/QUOTE symbol")
            sys.exit(2)
        ok = asyncio.run(
            _public_feed_check(venues, symbol), loop_factory=loop_factory()
        )
        if not ok:
            sys.exit(1)
        return
    if cmd == "live-engagement-audit":
        symbol = os.getenv("BOT_PREFLIGHT_SYMBOL", "BTC/USDT")
        if not symbol or "/" not in symbol:
            print("BOT_PREFLIGHT_SYMBOL must be a canonical BASE/QUOTE symbol")
            sys.exit(2)
        report_dir = Path(os.getenv(
            "ARBX_ENGAGEMENT_AUDIT_DIR",
            str(Path(__file__).resolve().parents[2] / "diagnostics"),
        ))
        ok = asyncio.run(
            _live_engagement_audit(symbol, report_dir), loop_factory=loop_factory()
        )
        if not ok:
            sys.exit(1)
        return
    if cmd == "required-live-venue-audit":
        symbol = os.getenv("BOT_PREFLIGHT_SYMBOL", "BTC/USDT")
        if not symbol or "/" not in symbol:
            print("BOT_PREFLIGHT_SYMBOL must be a canonical BASE/QUOTE symbol")
            sys.exit(2)
        report_dir = Path(os.getenv(
            "ARBX_ENGAGEMENT_AUDIT_DIR",
            str(Path(__file__).resolve().parents[2] / "diagnostics"),
        ))
        ok = asyncio.run(
            _required_live_venue_audit(symbol, report_dir),
            loop_factory=loop_factory(),
        )
        if not ok:
            sys.exit(1)
        return
    if cmd == "randomized-venue-feed-audit":
        report_dir = Path(os.getenv(
            "ARBX_ENGAGEMENT_AUDIT_DIR",
            str(Path(__file__).resolve().parents[2] / "diagnostics"),
        ))
        ok = asyncio.run(
            _randomized_venue_feed_audit(report_dir),
            loop_factory=loop_factory(),
        )
        if not ok:
            sys.exit(1)
        return
    if cmd == "readiness-state":
        from arbx.readiness_state import write_readiness_state

        report_dir = Path(os.getenv(
            "ARBX_ENGAGEMENT_AUDIT_DIR",
            str(Path(__file__).resolve().parents[2] / "diagnostics"),
        ))
        report = write_readiness_state(report_dir)
        print(
            f"READINESS={report['readinessState']} "
            f"LIVE_ENGAGEABLE={str(report['liveEngageable']).lower()} "
            f"FRESH_LIVE_VENUES={report['qualifiedLiveVenueCount']}"
        )
        for source_name, evidence in report["evidenceSources"].items():
            print(
                f"  {source_name}: {evidence['status']} "
                f"age_seconds={evidence['ageSeconds']}"
            )
        for venue in report["venues"]:
            if venue["livePermissionCandidate"]:
                print(
                    f"  [{venue['venue']}] public={venue['publicFeed']['status']} "
                    f"permissions={venue['permissionEvidence']['status']} "
                    f"same_pair={venue['authenticatedSamePairFeeds']['status']} "
                    "live_eligible=false"
                )
        print("Strategies:")
        for strategy in report["strategies"]:
            print(
                f"  {strategy['id']}: {strategy['connectionState']} "
                f"(live_engagement_verified=false)"
            )
        print(
            "Persisted read-only readiness state: "
            + str(report["persistenceValidation"]["artifactPaths"][-1])
        )
        print("No credentials, balances, orders, or transfers were read or submitted.")
        return
    if cmd == "permission-revalidate":
        from arbx.permissions import PERMISSION_PROBE_VENUES

        venues = tuple(argv[1:]) or tuple(sorted(PERMISSION_PROBE_VENUES))
        invalid = [venue for venue in venues if venue not in PERMISSION_PROBE_VENUES]
        if invalid or len(set(venues)) != len(venues):
            print("permission-revalidate requires unique supported venues: " +
                  ", ".join(sorted(PERMISSION_PROBE_VENUES)))
            sys.exit(2)
        report_dir = Path(os.getenv(
            "ARBX_PERMISSION_REPORT_DIR",
            str(Path(__file__).resolve().parents[2] / "diagnostics"),
        ))
        try:
            report = asyncio.run(
                _permission_revalidation(venues, report_dir),
                loop_factory=loop_factory(),
            )
        except KeyboardInterrupt:
            print("Permission revalidation interrupted; latest persistent report is retained.")
            sys.exit(130)
        for venue_id in venues:
            evidence = report["venues"][venue_id]
            print(
                f"[{venue_id}] STATE={evidence['state']} "
                f"ATTEMPTS={evidence['attempts']} "
                f"VALIDATED={str(evidence.get('validationComplete', False)).lower()} "
                f"LIVE_ELIGIBLE={str(evidence.get('liveEligible', False)).lower()}"
            )
            if evidence.get("policyBlockers"):
                print("  blockers: " + ", ".join(evidence["policyBlockers"]))
            if evidence.get("lastFailureType"):
                print("  last error class: " + evidence["lastFailureType"])
        print("Read-only permission probes finished. No balances, orders, or transfers were requested.")
        if not all(
            report["venues"][venue_id].get("validationComplete") is True
            for venue_id in venues
        ):
            sys.exit(1)
        return
    if cmd == "authenticated-feed-validation":
        from arbx.permissions import PERMISSION_PROBE_VENUES

        args = list(argv[1:])
        symbol = "BTC/USDT"
        if args and "/" in args[0]:
            symbol = args.pop(0)
        try:
            from arbx.authenticated_feed_validation import validate_pair_format
            symbol = validate_pair_format(symbol)
        except ValueError as exc:
            print(str(exc))
            sys.exit(2)
        venues = tuple(args) or tuple(
            venue for venue in sorted(PERMISSION_PROBE_VENUES)
            if os.getenv(f"BOT_{venue.upper()}_KEY")
            and (os.getenv(f"BOT_{venue.upper()}_SECRET")
                 or os.getenv(f"BOT_{venue.upper()}_PRIVATE_KEY"))
        )
        invalid = [venue for venue in venues if venue not in PERMISSION_PROBE_VENUES]
        if invalid or len(set(venues)) != len(venues):
            print("authenticated-feed-validation requires unique permission-probed venues: "
                  + ", ".join(sorted(PERMISSION_PROBE_VENUES)))
            sys.exit(2)
        if not venues:
            print("No permission-probed venues have local credentials configured; use the masked launcher.")
            sys.exit(2)
        report_dir = Path(os.getenv(
            "ARBX_FEED_VALIDATION_DIR",
            str(Path(__file__).resolve().parents[2] / "diagnostics"),
        ))
        try:
            report = asyncio.run(
                _authenticated_feed_validation(venues, symbol, report_dir),
                loop_factory=loop_factory(),
            )
        except KeyboardInterrupt:
            print("Feed validation interrupted; latest persistent report is retained.")
            sys.exit(130)
        print(
            f"Pair {symbol}: "
            f"{sum(item.get('strictPairValidated') is True for item in report['venues'].values())}"
            f"/{len(venues)} venue(s) passed permission + REST + WebSocket checks."
        )
        print("Read-only validation only; balances, orders, and transfers were not requested.")
        if not all(item.get("strictPairValidated") is True for item in report["venues"].values()):
            sys.exit(1)
        return
    if cmd == "hybrid-catalog":
        from arbx.hybrid.registry import VENUE_CATALOG
        from arbx.hybrid.strategies import strategy_catalog
        print("Hybrid venue catalog:", len(VENUE_CATALOG))
        for v in VENUE_CATALOG:
            print(f"  {v.id:<12} ccxt={v.ccxt_id:<12} adapter={v.adapter}")
        print("Strategy catalog (not a live-readiness claim):", ", ".join(strategy_catalog()))
        return
    if cmd == "hybrid-validate":
        cfg = Config.from_env()
        live_validation = os.getenv("BOT_HYBRID_LIVE", "0") == "1"
        cfg.mode = "live" if live_validation else "paper"
        if live_validation:
            os.environ["BOT_HYBRID_VALIDATION_ONLY"] = "1"
        cfg.validate()
        from arbx.hybrid.engine import HybridEngine
        engine = HybridEngine.create(cfg)
        requested = argv[1:] or [x.venue_id or x.id for x in cfg.exchanges]
        symbol = os.getenv("BOT_PREFLIGHT_SYMBOL", "BTC/USDT")
        notional = float(os.getenv("BOT_PREFLIGHT_NOTIONAL_USD", str(cfg.trade_size_usd)))

        async def _hybrid_validate():
            all_ok = True
            try:
                for venue in requested:
                    if venue not in engine.adapters:
                        print(f"[{venue}] ERROR not configured or not in venue catalog")
                        all_ok = False
                        continue
                    ev = await engine.validate_venue(venue, symbol=symbol, notional_usd=notional)
                    d = ev.evidence
                    print(
                        f"[{venue}] REST={str(ev.rest_ok).lower()} "
                        f"WS_PUBLIC={str(ev.public_ws_ok).lower()} "
                        f"WS_PRIVATE={str(ev.private_ws_ok).lower()} "
                        f"BALANCE={str(ev.balance_ok).lower()} "
                        f"PERMISSION={str(ev.permission_ok).lower()} "
                        f"EXECUTION={str(ev.execution_ok).lower()} "
                        f"DEPTH={str(ev.depth_ok).lower()} "
                        f"LIVE_ELIGIBLE={str(ev.live_eligible).lower()}"
                    )
                    if d.get("rest", {}).get("rttMs") is not None:
                        print(f"  REST RTT: {d['rest']['rttMs']} ms")
                    if d.get("publicWS", {}).get("rttMs") is not None:
                        print(f"  PUBLIC WS RTT: {d['publicWS']['rttMs']} ms")
                    if d.get("depth", {}).get("bestBid") is not None:
                        print(
                            f"  book: bid={d['depth']['bestBid']} "
                            f"ask={d['depth']['bestAsk']} "
                            f"depthUsd={d['depth'].get('depthUsd')}"
                        )
                    if ev.reasons:
                        print("  reasons:", ", ".join(ev.reasons))
                    all_ok = all_ok and ev.live_eligible
            finally:
                for adapter in engine.adapters.values():
                    try:
                        await adapter.close()
                    except Exception:
                        pass
            return all_ok

        ok = asyncio.run(_hybrid_validate(), loop_factory=loop_factory())
        if not ok:
            sys.exit(1)
        return
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
        print(__doc__ or "usage: run.py [run|preflight|live-preflight|probe|public-feed-check|selftest|hybrid-catalog|hybrid-validate|transfer-plan] [--headless]")


if __name__ == "__main__":
    main()
