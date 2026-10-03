import asyncio
import pathlib
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx import web_api


class FakeExchange:
    has = {"fetchTime": True, "watchBalance": True, "watchOrderBook": True}

    async def load_markets(self):
        return {}

    async def fetch_time(self):
        return 123

    def market(self, symbol):
        return {"spot": True, "contract": False}

    async def fetch_balance(self):
        return {"free": {"USDT": 10}, "used": {}, "total": {"USDT": 10}}

    async def watch_balance(self):
        return {"free": {}, "used": {}, "total": {}}

    async def watch_order_book(self, symbol, limit):
        return {"bids": [[100, 1]], "asks": [[101, 1]], "nonce": 1}


class ControlVerificationTests(unittest.TestCase):
    def test_scanner_and_live_flags_require_real_private_and_public_streams(self):
        permission = {"source": "fixture", "tradePermission": "enabled", "liveEligible": True}
        with patch.object(web_api, "inspect_permissions", new=AsyncMock(return_value=permission)):
            evidence, balances, book = asyncio.run(web_api._probe_exchange("binance", FakeExchange(), "BTC/USDT"))
        self.assertTrue(evidence["privateWebSocket"])
        self.assertTrue(evidence["publicWebSocket"])
        self.assertTrue(evidence["scannerEligible"])
        self.assertTrue(evidence["liveEligible"])
        self.assertEqual(balances["USDT"]["free"], 10)
        self.assertEqual(book["bestBid"], 100)

    def test_missing_private_stream_fails_scanner_and_live_eligibility(self):
        exchange = FakeExchange()
        exchange.has = {**exchange.has, "watchBalance": False}
        permission = {"source": "fixture", "tradePermission": "enabled", "liveEligible": True}
        with patch.object(web_api, "inspect_permissions", new=AsyncMock(return_value=permission)):
            evidence, _, _ = asyncio.run(web_api._probe_exchange("binance", exchange, "BTC/USDT"))
        self.assertFalse(evidence["privateWebSocket"])
        self.assertFalse(evidence["scannerEligible"])
        self.assertFalse(evidence["liveEligible"])

    def test_complete_connectivity_gets_evidence_state_separate_from_live_permission(self):
        permission = {"source": "fixture", "tradePermission": "unverified", "liveEligible": False}
        with patch.object(web_api, "inspect_permissions", new=AsyncMock(return_value=permission)):
            evidence, _, _ = asyncio.run(web_api._probe_exchange("bybit", FakeExchange(), "BTC/USDT"))
        self.assertEqual(evidence["connectionState"], "FULLY_VERIFIED")
        self.assertTrue(evidence["scannerEligible"])
        self.assertFalse(evidence["liveEligible"])
        self.assertTrue(evidence["verifiedAt"])

    def test_saved_verification_expires_after_five_minutes(self):
        from datetime import datetime, timezone
        now = time.time()
        timestamp = datetime.fromtimestamp(now - web_api.VERIFICATION_TTL_SECONDS - 1, timezone.utc).isoformat()
        self.assertFalse(web_api._verification_is_fresh(timestamp, now=now))
        self.assertTrue(web_api._verification_is_fresh(
            datetime.fromtimestamp(now - 30, timezone.utc).isoformat(), now=now))

    def test_exchange_diagnostics_redact_credentials_and_signature(self):
        class CredentialExchange:
            apiKey = "visible-api-key"
            secret = "visible-private-secret"
            password = "visible-passphrase"

        message = web_api._safe_exchange_error(
            RuntimeError("request failed apiKey=visible-api-key&signature=abc123 visible-private-secret visible-passphrase"),
            CredentialExchange(),
        )
        self.assertNotIn("visible-api-key", message)
        self.assertNotIn("visible-private-secret", message)
        self.assertNotIn("visible-passphrase", message)
        self.assertNotIn("abc123", message)

