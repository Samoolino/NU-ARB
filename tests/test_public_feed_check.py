import asyncio
import pathlib
import random
import sys
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.cli import (
    REQUIRED_LIVE_VENUES,
    _check_public_feed,
    _live_engagement_audit,
    _random_pair_venue_check,
    _randomized_venue_feed_audit,
    _required_live_venue_audit,
)


class FakePublicAdapter:
    def __init__(self, venue_id, credentials, ccxt_id=None):
        self.venue_id = venue_id
        self.credentials = credentials
        self.ccxt_id = ccxt_id
        self.ex = None
        self.closed = False

    async def connect(self):
        return None

    async def get_market(self, symbol):
        return {"spot": True, "contract": False}

    async def get_order_book(self, symbol, limit):
        return {"bids": [[100.0, 1.0]], "asks": [[101.0, 1.0]]}

    async def verify_public_stream(self, symbol, limit):
        return {
            "ok": True,
            "book": {
                "bids": [[100.0, 1.0]],
                "asks": [[101.0, 1.0]],
                "timestamp": 1_700_000_000_000,
                "nonce": 42,
            },
        }

    async def close(self):
        self.closed = True


class FakeRandomPairAdapter(FakePublicAdapter):
    def __init__(self, venue_id, credentials, ccxt_id=None):
        super().__init__(venue_id, credentials, ccxt_id)
        self.ex = SimpleNamespace(markets={
            "BTC/USDT": {
                "symbol": "BTC/USDT", "spot": True, "contract": False,
                "active": True, "base": "BTC", "quote": "USDT",
            },
            "BTC/ABC": {
                "symbol": "BTC/ABC", "spot": False, "contract": True,
                "active": True, "base": "BTC", "quote": "ABC",
            },
            "ETH/USDC": {
                "symbol": "ETH/USDC", "spot": True, "contract": False,
                "active": True, "base": "ETH", "quote": "USDC",
            },
        })


class PublicFeedCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_verifies_read_only_rest_and_websocket_books(self):
        with patch("arbx.hybrid.adapters.CCXTProAdapter", FakePublicAdapter):
            result = await _check_public_feed("binance", "BTC/USDT")

        self.assertTrue(result["restOk"])
        self.assertTrue(result["websocketOk"])
        self.assertTrue(result["verified"])
        self.assertFalse(result["authenticated"])
        self.assertFalse(result["ordersSubmitted"])
        self.assertEqual(result["sequence"], 42)

    async def test_non_spot_market_fails_closed(self):
        class DerivativesOnlyAdapter(FakePublicAdapter):
            async def get_market(self, symbol):
                return {"spot": False, "contract": True}

        with patch("arbx.hybrid.adapters.CCXTProAdapter", DerivativesOnlyAdapter):
            result = await _check_public_feed("mexc", "BTC/USDT")

        self.assertFalse(result.get("verified", False))
        self.assertEqual(result["reason"], "spot_market_unavailable")
        self.assertFalse(result["ordersSubmitted"])

    async def test_missing_rest_or_websocket_sides_do_not_verify(self):
        class IncompleteBookAdapter(FakePublicAdapter):
            async def get_order_book(self, symbol, limit):
                return {"bids": [[100.0, 1.0]], "asks": []}

        with patch("arbx.hybrid.adapters.CCXTProAdapter", IncompleteBookAdapter):
            result = await _check_public_feed("kucoin", "BTC/USDT")

        self.assertFalse(result["restOk"])
        self.assertTrue(result["websocketOk"])
        self.assertFalse(result["verified"])

    async def test_random_pair_probe_selects_only_active_spot_markets(self):
        from arbx.hybrid.registry import VenueSpec

        spec = VenueSpec("binance", "binance")
        with patch("arbx.hybrid.adapters.CCXTProAdapter", FakeRandomPairAdapter):
            result = await _random_pair_venue_check(
                spec, random.Random(7), asyncio.Semaphore(1)
            )

        self.assertTrue(result["adapterConstructorAvailable"])
        self.assertIn(result["symbol"], {"BTC/USDT", "ETH/USDC"})
        self.assertTrue(result["restOk"])
        self.assertTrue(result["websocketOk"])
        self.assertTrue(result["verified"])
        self.assertFalse(result["authenticated"])
        self.assertFalse(result["ordersSubmitted"])

    async def test_random_pair_probe_records_redacted_transport_failures_and_closes(self):
        from arbx.hybrid.registry import VenueSpec

        instances = []

        class TimeoutAdapter(FakeRandomPairAdapter):
            def __init__(self, venue_id, credentials, ccxt_id=None):
                super().__init__(venue_id, credentials, ccxt_id)
                self.ex = SimpleNamespace(apiKey="public-audit-test-key", secret="public-audit-test-secret")
                instances.append(self)

            async def connect(self):
                raise TimeoutError("request failed public-audit-test-secret")

        spec = VenueSpec("binance", "binance")
        with patch("arbx.hybrid.adapters.CCXTProAdapter", TimeoutAdapter):
            result = await _random_pair_venue_check(
                spec, random.Random(7), asyncio.Semaphore(1)
            )

        self.assertFalse(result["verified"])
        self.assertEqual(result["failedStage"], "market_discovery")
        self.assertIn("TimeoutError", result["reason"])
        self.assertNotIn("public-audit-test-secret", result["reason"])
        self.assertTrue(instances[0].closed)

    async def test_engagement_audit_persists_fail_closed_evidence(self):
        from arbx.hybrid.registry import VenueSpec

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("arbx.hybrid.registry.VENUE_CATALOG", (
                    VenueSpec("testvenue", "testexchange"),
                )),
                patch("arbx.hybrid.networks.MAJOR_NETWORKS", ()),
                patch("arbx.hybrid.strategies.strategy_catalog", return_value=("cross_exchange",)),
                patch("arbx.cli._check_public_feed", new=AsyncMock(return_value={
                    "venue": "testvenue",
                    "symbol": "BTC/USDT",
                    "verified": True,
                    "restOk": True,
                    "websocketOk": True,
                })),
            ):
                result = await _live_engagement_audit("BTC/USDT", pathlib.Path(directory))

            self.assertTrue(result)
            report_path = pathlib.Path(directory) / "live-engagement-audit-latest.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(report["liveEngageable"])
            self.assertEqual(report["liveModeState"], "BLOCKED_NOT_AUTHENTICATED")
            self.assertFalse(report["priceDifferenceProfitAssurance"])
            self.assertTrue(report["persistenceValidation"]["validated"])
            self.assertFalse(report["venues"][0]["authenticatedBalanceVerified"])
            self.assertFalse(report["venues"][0]["liveEngageable"])
            self.assertFalse(report["strategies"][0]["live_execution_verified"])

    async def test_required_venue_audit_persists_each_blocker_and_feed_failure(self):
        async def fake_feed(venue_id, symbol, ccxt_id=None):
            passed = venue_id != "kucoin"
            return {
                "venue": venue_id,
                "symbol": symbol,
                "verified": passed,
                "restOk": passed,
                "websocketOk": passed,
                "failedStage": None if passed else "market_discovery",
                "reason": None if passed else "RequestTimeout: market metadata",
                "authenticated": False,
                "ordersSubmitted": False,
            }

        with tempfile.TemporaryDirectory() as directory:
            with patch("arbx.cli._check_public_feed", new=AsyncMock(side_effect=fake_feed)):
                result = await _required_live_venue_audit(
                    "BTC/USDT", pathlib.Path(directory)
                )

            report_path = pathlib.Path(directory) / "required-live-venue-audit-latest.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertFalse(result)
        self.assertEqual(report["requiredVenues"], list(REQUIRED_LIVE_VENUES))
        self.assertEqual(
            [venue["venue"] for venue in report["venues"]],
            list(REQUIRED_LIVE_VENUES),
        )
        self.assertTrue(report["persistenceValidation"]["validated"])
        self.assertFalse(report["liveEngageable"])
        self.assertEqual(report["liveModeState"], "BLOCKED_REQUIRED_EVIDENCE_MISSING")
        kucoin = next(venue for venue in report["venues"] if venue["venue"] == "kucoin")
        self.assertIn(
            "feed_failure:market_discovery:RequestTimeout: market metadata",
            kucoin["blockers"],
        )
        self.assertIn(
            "authenticated_credentials_and_account_scope_not_verified",
            kucoin["blockers"],
        )
        self.assertFalse(kucoin["liveExecutionRouteVerified"])

    async def test_randomized_audit_persists_sampled_pairs_and_live_blockers(self):
        from arbx.hybrid.registry import VenueSpec

        async def fake_check(spec, rng, semaphore):
            return {
                "venue": spec.id,
                "adapter": spec.adapter,
                "ccxtId": spec.ccxt_id,
                "adapterConstructorAvailable": True,
                "symbol": f"{spec.id.upper()}/USDT",
                "restOk": True,
                "websocketOk": True,
                "verified": True,
                "authenticated": False,
                "ordersSubmitted": False,
            }

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("arbx.hybrid.registry.VENUE_CATALOG", (
                    VenueSpec("binance", "binance"),
                    VenueSpec("mexc", "mexc"),
                )),
                patch("arbx.cli._random_pair_venue_check", new=AsyncMock(side_effect=fake_check)),
            ):
                result = await _randomized_venue_feed_audit(pathlib.Path(directory))

            report = json.loads(
                (pathlib.Path(directory) / "randomized-venue-feed-audit-latest.json")
                .read_text(encoding="utf-8")
            )

        self.assertTrue(result)
        self.assertTrue(report["persistenceValidation"]["validated"])
        self.assertEqual(report["selection"]["onePairPerVenue"], True)
        self.assertEqual(set(report["selection"]["venueSeeds"]), {"binance", "mexc"})
        self.assertEqual([item["venue"] for item in report["venues"]], ["binance", "mexc"])
        self.assertEqual([item["symbol"] for item in report["venues"]], ["BINANCE/USDT", "MEXC/USDT"])
        self.assertFalse(report["liveEngageable"])
        self.assertFalse(report["priceDifferenceProfitAssurance"])
        self.assertIn(
            "authenticated_account_scope_not_verified",
            report["venues"][0]["liveBlockers"],
        )


if __name__ == "__main__":
    unittest.main()
