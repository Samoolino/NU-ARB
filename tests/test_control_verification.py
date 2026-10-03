import asyncio
import pathlib
import sys
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
