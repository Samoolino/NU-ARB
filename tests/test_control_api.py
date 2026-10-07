import os
import pathlib
import sys
import asyncio
import json
import time
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx import web_api


class ControlApiTests(unittest.TestCase):
    def test_live_cross_selection_requires_multiple_venues(self):
        with self.assertRaises(ValueError):
            web_api.EngineStart(mode="live", exchange_ids=["binance"], trade_size_usd=5,
                                max_loss_usd=2, target_profit_usd=1, cross_live=True)

        payload = web_api.EngineStart(mode="live", exchange_ids=["binance", "bybit"], trade_size_usd=5,
                                      max_loss_usd=2, target_profit_usd=1, cross_live=True)
        self.assertTrue(payload.cross_live)

    def test_all_registered_venues_can_be_selected_together(self):
        exchange_ids = list(web_api.VENUES)
        self.assertEqual(len(exchange_ids), 18)
        payload = web_api.EngineStart(
            mode="paper", exchange_ids=exchange_ids, trade_size_usd=5,
            max_loss_usd=2, target_profit_usd=1,
        )
        self.assertEqual(payload.exchange_ids, exchange_ids)
        with self.assertRaises(ValueError):
            web_api.EngineStart(
                mode="paper", exchange_ids=exchange_ids + ["unknown"], trade_size_usd=5,
                max_loss_usd=2, target_profit_usd=1,
            )

    def test_catalog_metadata_includes_unavailable_venues_without_enabling_them(self):
        self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "catalog@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        response = self.client.get("/api/v1/exchanges", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        venues = response.json()["exchanges"]
        self.assertEqual(
            [venue["catalogId"] for venue in venues],
            [venue.id for venue in web_api.VENUE_CATALOG],
        )
        for venue in venues:
            self.assertEqual(venue["catalogState"], "CATALOGUED")
            self.assertEqual(venue["lifecycleState"], "CATALOGUED")
            self.assertTrue(all(value is False for value in venue["verificationStates"].values()))
            self.assertFalse(venue["engineSelectionAvailable"])
            self.assertEqual(venue["engineSelectionStatus"], "UNAVAILABLE")

        by_id = {venue["catalogId"]: venue for venue in venues}
        for venue_id in ("bitmart", "upbit"):
            venue = by_id[venue_id]
            self.assertFalse(venue["engineSelectionSupported"])
            self.assertFalse(venue["adapterAvailable"])
            self.assertEqual(venue["authenticationModes"], [])
            self.assertEqual(venue["controlApiStatus"], "UNAVAILABLE")
            verification = self.client.post(
                f"/api/v1/exchanges/{venue_id}/verify",
                headers=self.headers,
                json={"auth_mode": "ccxt", "credentials": {"apiKey": "test", "secret": "test"}},
            )
            self.assertEqual(verification.status_code, 404)
            engine_start = self.client.post("/api/v1/engine/start", headers=self.headers, json={
                "mode": "paper", "exchange_ids": ["binance", venue_id],
                "trade_size_usd": 5, "max_loss_usd": 2, "target_profit_usd": 1,
            })
            self.assertEqual(engine_start.status_code, 422)

    def setUp(self):
        self.db_path = pathlib.Path(__file__).resolve().parent / ".control-test.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            candidate = pathlib.Path(str(self.db_path) + suffix)
            if candidate.exists():
                candidate.unlink()
        self.original_db = web_api.APP_DB
        web_api.APP_DB = self.db_path
        self.env = patch.dict(os.environ, {
            "ENGINE_PROXY_TOKEN": "test-service-token",
            "CREDENTIAL_ENCRYPTION_KEY": "ab" * 32,
            "APP_SECURE_COOKIE": "0",
            "ARBX_LIVE_TRADING_ENABLED": "0",
        }, clear=False)
        self.env.start()
        self.client_context = TestClient(web_api.app)
        self.client = self.client_context.__enter__()
        self.headers = {"X-Engine-Token": "test-service-token"}

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        web_api.APP_DB = self.original_db
        self.env.stop()
        for suffix in ("", "-wal", "-shm"):
            candidate = pathlib.Path(str(self.db_path) + suffix)
            if candidate.exists():
                candidate.unlink()

    def test_service_proxy_token_is_required(self):
        response = self.client.get("/api/v1/runtime")
        self.assertEqual(response.status_code, 503)

    def test_signup_creates_session_and_health_checks_durable_db(self):
        response = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "person@example.com", "password": "very-long-test-password",
            "password_confirmation": "very-long-test-password",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=self.headers).json()["user"]["email"], "person@example.com")
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_live_engine_is_operator_disabled_by_default(self):
        response = self.client.post("/api/v1/engine/start", headers=self.headers, json={
            "mode": "live", "exchange_ids": ["binance", "bybit"], "trade_size_usd": 5,
            "max_loss_usd": 2, "target_profit_usd": 1, "cross_live": True,
            "live_confirmation": "I ACCEPT REAL ORDERS",
        })
        self.assertEqual(response.status_code, 503)
        self.assertIn("disabled", response.json()["detail"].lower())

    def test_global_kill_switch_revokes_orders_after_api_engine_start(self):
        from arbx.execution_gate import ExecutionEvidence, OrderScope, opportunity_fingerprint
        from arbx.execute import CrossExecutor, LegFailure

        self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "post-start-kill@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        db = web_api._connect()
        try:
            uid = db.execute("SELECT id FROM users WHERE email=?", ("post-start-kill@example.com",)).fetchone()[0]
            for venue in ("binance", "bybit"):
                encrypted = web_api._encrypt(json.dumps(
                    {"apiKey": "test-key", "secret": "test-secret"}
                ).encode())
                db.execute(
                    "INSERT INTO exchange_credentials(user_id,exchange_id,encrypted_credentials,auth_mode,state,"
                    "last_verified,verification_json) VALUES(?,?,?,?,?,?,?)",
                    (uid, venue, encrypted, "hmac", "FULLY_VERIFIED", "2026-10-07T00:00:00+00:00", "{}"),
                )
            db.commit()
        finally:
            db.close()

        switches = {
            name: "1" for name in os.environ if name.startswith("BOT_ALLOW_")
        }
        switches.update({
            "BOT_MODE": "live", "BOT_ALLOW_ORDERS": "1",
            "BOT_CROSS_LIVE": "1", "ARBX_LIVE_TRADING_ENABLED": "1",
        })

        runner_started = threading.Event()
        runner_finished = threading.Event()

        async def hold_engine(*_args):
            runner_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                current = web_api.engine_hub
                if current is not None:
                    current.journal_store.close()
                runner_finished.set()

        original_state = {
            "engine_owner_id": web_api.engine_owner_id,
            "engine_hub": web_api.engine_hub,
            "engine_task": web_api.engine_task,
            "engine_phase": web_api.engine_phase,
            "engine_error": web_api.engine_error,
            "engine_stop_requested": web_api.engine_stop_requested,
        }
        try:
            web_api.engine_owner_id = None
            web_api.engine_hub = None
            web_api.engine_task = None
            web_api.engine_phase = "STOPPED"
            web_api.engine_error = None
            web_api.engine_stop_requested = False
            with patch.dict(os.environ, switches, clear=False), \
                    patch.object(web_api, "_run_engine_job", new=hold_engine):
                response = self.client.post("/api/v1/engine/start", headers=self.headers, json={
                    "mode": "live", "exchange_ids": ["binance", "bybit"],
                    "trade_size_usd": 5, "max_loss_usd": 2, "target_profit_usd": 1,
                    "cross_live": True, "live_confirmation": "I ACCEPT REAL ORDERS",
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(runner_started.wait(timeout=5))
                hub = web_api.engine_hub
                opportunity = SimpleNamespace(
                    buy_ex="buy", sell_ex="sell", symbol="BTC/USDT", base=0.5,
                    limit_buy=100.0, limit_sell=102.0, cost=50.0,
                    expected_usd=1.0, worst_usd=0.5, net_bps=10.0, worst_bps=5.0,
                    quote_ccy="USDT",
                )
                permit = hub.execution_gate.authorize(
                    "cross", venue_ids=("buy", "sell"), route_id="cross:buy>sell",
                    opportunity_id=opportunity_fingerprint("cross", opportunity),
                    evidence=ExecutionEvidence(
                        authenticated_credentials=True, venue_live_eligible=True,
                        execution_eligible=True, active_market=True, fresh_orderbook=True,
                        sufficient_depth=True, sufficient_balance=True, sufficient_capital=True,
                        known_fees=True, latency_acceptable=True, risk_approved=True,
                        rate_limit_ok=True, venue_healthy=True, route_certified=True,
                        expected_pnl=1.0, min_expected_pnl=0.01,
                        worst_case_pnl=0.5, min_worst_case_pnl=0.01,
                        expected_bps=10.0, min_expected_bps=3.0,
                        worst_case_bps=5.0, min_worst_case_bps=0.5,
                        orderbook_observed_at=time.monotonic(), max_book_age_ms=10_000,
                    ),
                    orders=(
                        OrderScope(0, "buy", "BTC/USDT", "buy", "limit", 0.5, 100.0),
                        OrderScope(1, "sell", "BTC/USDT", "sell", "limit", 0.5, 102.0),
                    ),
                )

                class FakeExchange:
                    def __init__(self):
                        self.orders = []

                    def amount_to_precision(self, _symbol, amount):
                        return str(amount)

                    async def create_order(self, *args, **_kwargs):
                        self.orders.append(args)
                        return {"filled": 0.5, "status": "closed", "cost": 50.0}

                buy, sell = FakeExchange(), FakeExchange()
                os.environ["ARBX_LIVE_TRADING_ENABLED"] = "0"
                with self.assertRaisesRegex(LegFailure, "ARBX_LIVE_TRADING_ENABLED"):
                    asyncio.run(CrossExecutor({"buy": buy, "sell": sell}, hub.cfg, hub.execution_gate)
                                .execute(opportunity, permit))
                self.assertEqual(buy.orders, [])
                self.assertEqual(sell.orders, [])
                if web_api.engine_task and not web_api.engine_task.done():
                    web_api.engine_task.get_loop().call_soon_threadsafe(web_api.engine_task.cancel)
                journal_path = hub.journal_store.path
                self.assertTrue(runner_finished.wait(timeout=5))
                for suffix in ("", "-wal", "-shm"):
                    candidate = pathlib.Path(str(journal_path) + suffix)
                    if candidate.exists():
                        candidate.unlink()
        finally:
            for name, value in original_state.items():
                setattr(web_api, name, value)

    def test_validation_errors_do_not_echo_submitted_secrets(self):
        response = self.client.post("/api/v1/auth/login", headers=self.headers, json={
            "email": "bad", "password": "do-not-echo-this-value",
        })
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("do-not-echo-this-value", response.text)

    def test_saved_exchange_credentials_are_encrypted_and_not_returned(self):
        signup = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "trader@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        self.assertEqual(signup.status_code, 200)

        class FakeAdapter:
            async def close(self):
                return None

        evidence = {"authentication": True, "rest": True, "account": True, "balances": True, "privateWebSocket": True,
                    "publicWebSocket": True, "scannerEligible": True, "liveEligible": False,
                    "connectionState": "FULLY_VERIFIED", "verifiedAt": "2026-10-03T00:00:00+00:00"}
        with patch.object(web_api, "_make_exchange", return_value=FakeAdapter()), \
             patch.object(web_api, "_probe_exchange", return_value=(evidence, {"USDT": {"free": 1, "used": 0, "total": 1}}, None)):
            response = self.client.post("/api/v1/exchanges/binance/verify", headers=self.headers, json={
                "auth_mode": "hmac", "symbol": "BTC/USDT",
                "credentials": {"apiKey": "public-test-key", "secret": "private-test-secret"},
            })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("private-test-secret", response.text)
        db = web_api._connect()
        try:
            stored = db.execute(
                "SELECT encrypted_credentials FROM exchange_credentials").fetchone()[0]
        finally:
            db.close()
        self.assertNotIn(b"private-test-secret", stored)
        self.assertIn(b"private-test-secret", web_api._decrypt(stored))

    def test_capital_sources_returns_empty_until_a_fresh_verified_balance_exists(self):
        self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "capital@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        response = self.client.get("/api/v1/capital-sources", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"sources": [], "fundsMoved": False})

    def test_engine_cannot_bypass_authenticated_stream_requirement(self):
        self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "paper@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        response = self.client.post("/api/v1/engine/start", headers=self.headers, json={
            "mode": "paper", "exchange_ids": ["binance"], "trade_size_usd": 5,
            "max_loss_usd": 2, "target_profit_usd": 1, "require_private_stream": False,
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn("private balance stream", response.json()["detail"])

    def test_credentials_cannot_be_changed_during_owned_engine_session(self):
        signup = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "active@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        self.assertEqual(signup.status_code, 200)

        class ActiveTask:
            def done(self):
                return False

        with patch.object(web_api, "engine_owner_id", signup.json()["user"]["id"]), \
             patch.object(web_api, "engine_task", ActiveTask()), \
             patch.object(web_api, "_make_exchange") as make_exchange:
            response = self.client.post("/api/v1/exchanges/binance/verify", headers=self.headers, json={
                "auth_mode": "hmac", "symbol": "BTC/USDT",
                "credentials": {"apiKey": "public-test-key", "secret": "private-test-secret"},
            })
        self.assertEqual(response.status_code, 409)
        make_exchange.assert_not_called()
