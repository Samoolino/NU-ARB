import asyncio
import io
import pathlib
import sys
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx import cli, web_api
from arbx.config import Config, ExchangeCfg
from arbx.market import orderbook_limit
from arbx.worker import spot_market_options


class FakeExchange:
    has = {"fetchTime": True, "watchBalance": True, "watchOrderBook": True,
            "createOrder": True, "createMarketOrder": True, "fetchOrder": True}

    def feature_value(self, symbol, method, param):
        return {"IOC": True}

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

    async def create_order(self, *args, **kwargs):
        raise AssertionError("read-only preflight attempted to place an order")


class ControlVerificationTests(unittest.TestCase):
    def test_orderbook_limits_match_bybit_and_bitfinex_constraints(self):
        self.assertEqual(orderbook_limit("bybit", 10), 50)
        self.assertEqual(orderbook_limit("htx", 10), 20)
        self.assertEqual(orderbook_limit("bitfinex", 10), 25)
        self.assertEqual(orderbook_limit("binance", 10), 10)

    def test_exchange_market_loading_is_spot_only(self):
        self.assertEqual(spot_market_options("htx")["fetchMarkets"]["types"],
                         {"spot": True, "linear": False, "inverse": False})
        self.assertEqual(spot_market_options("kucoin")["fetchMarkets"]["types"], ["spot"])

    def test_scanner_and_live_flags_require_real_private_and_public_streams(self):
        permission = {"source": "fixture", "tradePermission": "enabled", "liveEligible": True}
        with patch.object(web_api, "inspect_permissions", new=AsyncMock(return_value=permission)):
            evidence, balances, book = asyncio.run(web_api._probe_exchange("binance", FakeExchange(), "BTC/USDT"))
        self.assertTrue(evidence["privateWebSocket"])
        self.assertTrue(evidence["publicWebSocket"])
        self.assertTrue(evidence["scannerEligible"])
        self.assertTrue(evidence["executionEligible"])
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

    def test_incomplete_rest_balance_snapshot_fails_authenticated_scanner(self):
        exchange = FakeExchange()
        exchange.fetch_balance = AsyncMock(return_value={"free": {"USDT": 1}})
        permission = {"source": "fixture", "tradePermission": "enabled", "liveEligible": True}
        with patch.object(web_api, "inspect_permissions", new=AsyncMock(return_value=permission)):
            evidence, balances, _ = asyncio.run(
                web_api._probe_exchange("binance", exchange, "BTC/USDT"))
        self.assertFalse(evidence["balances"])
        self.assertFalse(evidence["scannerEligible"])
        self.assertFalse(evidence["liveEligible"])
        self.assertIsNone(balances)

    def test_live_eligibility_requires_declared_spot_ioc_and_order_fetch(self):
        exchange = FakeExchange()
        exchange.has = {**exchange.has, "createOrder": False}
        permission = {"source": "fixture", "tradePermission": "enabled", "liveEligible": True}
        with patch.object(web_api, "inspect_permissions", new=AsyncMock(return_value=permission)):
            evidence, _, _ = asyncio.run(web_api._probe_exchange("binance", exchange, "BTC/USDT"))
        self.assertTrue(evidence["scannerEligible"])
        self.assertFalse(evidence["executionEligible"])
        self.assertFalse(evidence["liveEligible"])

    def test_live_eligibility_requires_market_order_unwind_capability(self):
        exchange = FakeExchange()
        exchange.has = {**exchange.has, "createMarketOrder": False}
        permission = {"source": "fixture", "tradePermission": "enabled", "liveEligible": True}
        with patch.object(web_api, "inspect_permissions", new=AsyncMock(return_value=permission)):
            evidence, _, _ = asyncio.run(web_api._probe_exchange("binance", exchange, "BTC/USDT"))
        self.assertTrue(evidence["scannerEligible"])
        self.assertFalse(evidence["executionEligible"])
        self.assertFalse(evidence["liveEligible"])

    def test_multi_venue_live_preflight_checks_every_exchange_before_engine_start(self):
        payload = web_api.EngineStart(mode="live", exchange_ids=["binance", "bybit"], trade_size_usd=5,
                                      max_loss_usd=2, target_profit_usd=1, cross_live=True,
                                      live_confirmation="I ACCEPT REAL ORDERS")
        hub = SimpleNamespace(run=AsyncMock(), stats=SimpleNamespace(status="RUNNING (LIVE)"))
        probe = AsyncMock(side_effect=[
            ({"liveEligible": True, "scannerEligible": True}, {}, {}),
            ({"liveEligible": False, "scannerEligible": True}, {}, {}),
        ])
        with patch.object(web_api, "engine_hub", hub), \
                patch.object(web_api, "engine_stop_requested", False), \
                patch.object(web_api, "engine_phase", "STARTING"), \
                patch.object(web_api, "engine_error", None), \
                patch.object(web_api, "_make_exchange", side_effect=[FakeExchange(), FakeExchange()]), \
                patch.object(web_api, "_probe_exchange", new=probe):
            asyncio.run(web_api._run_engine_job(
                payload, {"binance": {"apiKey": "test"}, "bybit": {"apiKey": "test"}},
                {"binance": "hmac", "bybit": "ccxt"},
            ))

        self.assertEqual([call.args[0] for call in probe.await_args_list], ["binance", "bybit"])
        hub.run.assert_not_awaited()

    def test_multi_venue_live_preflight_starts_only_after_all_venues_pass(self):
        payload = web_api.EngineStart(mode="live", exchange_ids=["binance", "bybit"], trade_size_usd=5,
                                      max_loss_usd=2, target_profit_usd=1, cross_live=True,
                                      live_confirmation="I ACCEPT REAL ORDERS")
        hub = SimpleNamespace(run=AsyncMock(), stats=SimpleNamespace(status="RUNNING (LIVE)"))
        probe = AsyncMock(return_value=({"liveEligible": True, "scannerEligible": True}, {}, {}))
        with patch.object(web_api, "engine_hub", hub), \
                patch.object(web_api, "engine_stop_requested", False), \
                patch.object(web_api, "engine_phase", "STARTING"), \
                patch.object(web_api, "engine_error", None), \
                patch.object(web_api, "_make_exchange", side_effect=[FakeExchange(), FakeExchange()]), \
                patch.object(web_api, "_probe_exchange", new=probe):
            asyncio.run(web_api._run_engine_job(
                payload, {"binance": {"apiKey": "test"}, "bybit": {"apiKey": "test"}},
                {"binance": "hmac", "bybit": "ccxt"},
            ))

        self.assertEqual([call.args[0] for call in probe.await_args_list], ["binance", "bybit"])
        hub.run.assert_awaited_once()

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

    def test_terminal_preflight_is_read_only_and_keeps_unverified_permissions_ineligible(self):
        cfg = Config(exchanges=[ExchangeCfg(id="bybit", api_key="test-key", secret="test-secret")])
        exchange = FakeExchange()
        evidence = {"connectionState": "FULLY_VERIFIED", "scannerEligible": True,
                    "executionEligible": True, "liveEligible": False, "tradePermission": "unverified",
                    "withdrawalsDisabled": "unverified",
                    "permissions": {"source": "no supported authenticated key-scope endpoint"}}
        output = io.StringIO()
        with patch.object(exchange, "create_order", new_callable=AsyncMock) as create_order, \
            patch.object(web_api, "_make_exchange", return_value=exchange), \
                patch.object(web_api, "_probe_exchange", new=AsyncMock(return_value=(
                    evidence, {"USDT": {"free": 10.0, "total": 10.0}},
                    {"symbol": "BTC/USDT", "bestBid": 100, "bestAsk": 101}))), \
                redirect_stdout(output):
            ready = asyncio.run(cli._account_preflight(cfg, "BTC/USDT"))
            live_ready = asyncio.run(cli._account_preflight(cfg, "BTC/USDT", require_live=True))

        self.assertTrue(ready)
        self.assertFalse(live_ready)
        self.assertIn("READ-ONLY PREFLIGHT", output.getvalue())
        self.assertIn("liveEligible=false", output.getvalue())
        self.assertIn("tradePermission=unverified", output.getvalue())
        create_order.assert_not_called()
