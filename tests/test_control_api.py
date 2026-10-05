import json
import os
import pathlib
import sys
import unittest
from datetime import datetime, timezone
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


    def test_refresh_reconnects_saved_key_and_persists_balances(self):
        signup = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "refresh@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        self.assertEqual(signup.status_code, 200)

        class FakeAdapter:
            async def close(self):
                return None

        evidence = {"authentication": True, "rest": True, "account": True, "balances": True,
                    "privateWebSocket": True, "publicWebSocket": True, "scannerEligible": True,
                    "executionEligible": True, "liveEligible": True,
                    "connectionState": "FULLY_VERIFIED",
                    "permissions": {"liveEligible": True},
                    "verifiedAt": "2026-10-05T00:00:00+00:00"}
        balances = {"USDT": {"free": 123.45, "used": 4.0, "total": 127.45}}
        with patch.object(web_api, "_make_exchange", return_value=FakeAdapter()),              patch.object(web_api, "_probe_exchange", return_value=(evidence, balances, None)):
            verified = self.client.post("/api/v1/exchanges/binance/verify", headers=self.headers, json={
                "auth_mode": "hmac", "symbol": "BTC/USDT",
                "credentials": {"apiKey": "refresh-key", "secret": "refresh-secret"},
            })
            self.assertEqual(verified.status_code, 200)
            refreshed = self.client.post("/api/v1/exchanges/binance/refresh", headers=self.headers)
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.json()["balances"]["USDT"]["total"], 127.45)
        listing = self.client.get("/api/v1/exchanges", headers=self.headers)
        self.assertEqual(listing.status_code, 200)
        venue = next(item for item in listing.json()["exchanges"] if item["id"] == "binance")
        self.assertTrue(venue["credentialConnection"]["adapterConnected"])
        self.assertTrue(venue["credentialConnection"]["balanceConnected"])
        self.assertEqual(venue["balances"]["USDT"]["free"], 123.45)


    def test_non_scope_venue_can_be_promoted_with_explicit_trade_only_attestation(self):
        signup = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "attested@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        self.assertEqual(signup.status_code, 200)

        class FakeAdapter:
            async def close(self):
                return None

        evidence = {"authentication": True, "rest": True, "account": True, "balances": True,
                    "privateWebSocket": True, "publicWebSocket": True, "scannerEligible": True,
                    "executionEligible": True, "liveEligible": False,
                    "connectionState": "FULLY_VERIFIED",
                    "permissions": {"liveEligible": False},
                    "verifiedAt": "2026-10-05T00:00:00+00:00"}
        balances = {"USDT": {"free": 9.0, "used": 1.0, "total": 10.0}}
        with patch.object(web_api, "_make_exchange", return_value=FakeAdapter()),              patch.object(web_api, "_probe_exchange", return_value=(evidence, balances, None)):
            verified = self.client.post("/api/v1/exchanges/htx/verify", headers=self.headers, json={
                "auth_mode": "ccxt", "symbol": "BTC/USDT",
                "credentials": {"apiKey": "attested-key", "secret": "attested-secret"},
            })
            self.assertEqual(verified.status_code, 200)
            promoted = self.client.post("/api/v1/exchanges/htx/promote-live-ready",
                                        headers=self.headers,
                                        json={"confirmation": "PROMOTE LIVE READY",
                                              "permission_attestation": "I CONFIRM TRADE-ONLY API KEY"})
        self.assertEqual(promoted.status_code, 200)
        self.assertEqual(promoted.json()["state"], "LIVE_READY")
        self.assertEqual(promoted.json()["liveReady"], True)

    def test_live_ready_promotion_reconnects_key_before_state_change(self):
        signup = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "promote@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        self.assertEqual(signup.status_code, 200)

        class FakeAdapter:
            async def close(self):
                return None

        evidence = {"authentication": True, "rest": True, "account": True, "balances": True,
                    "privateWebSocket": True, "publicWebSocket": True, "scannerEligible": True,
                    "executionEligible": True, "liveEligible": True,
                    "connectionState": "FULLY_VERIFIED",
                    "permissions": {"liveEligible": True},
                    "verifiedAt": "2026-10-05T00:00:00+00:00"}
        balances = {"USDT": {"free": 9.0, "used": 1.0, "total": 10.0}}
        with patch.object(web_api, "_make_exchange", return_value=FakeAdapter()) as make_exchange,              patch.object(web_api, "_probe_exchange", return_value=(evidence, balances, None)) as probe:
            verified = self.client.post("/api/v1/exchanges/binance/verify", headers=self.headers, json={
                "auth_mode": "hmac", "symbol": "BTC/USDT",
                "credentials": {"apiKey": "promote-key", "secret": "promote-secret"},
            })
            self.assertEqual(verified.status_code, 200)
            promoted = self.client.post("/api/v1/exchanges/binance/promote-live-ready",
                                        headers=self.headers,
                                        json={"confirmation": "PROMOTE LIVE READY"})
        self.assertEqual(promoted.status_code, 200)
        self.assertEqual(promoted.json()["state"], "LIVE_READY")
        self.assertEqual(promoted.json()["balances"]["USDT"]["total"], 10.0)
        self.assertGreaterEqual(make_exchange.call_count, 2)
        self.assertGreaterEqual(probe.call_count, 2)
    def test_live_activation_readiness_requires_two_fresh_live_ready_venues(self):
        signup = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "activation-ready@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        self.assertEqual(signup.status_code, 200)
        uid = signup.json()["user"]["id"]
        evidence = {
            "scannerEligible": True, "executionEligible": True, "liveEligible": True,
            "livePermissionMode": "verified",
        }
        db = web_api._connect()
        try:
            for exchange_id in ("binance", "bybit"):
                encrypted = web_api._encrypt(json.dumps({"apiKey": "k", "secret": "s"}).encode())
                db.execute(
                    "INSERT INTO exchange_credentials(user_id,exchange_id,encrypted_credentials,auth_mode,state,last_verified,verification_json) VALUES(?,?,?,?,?,?,?)",
                    (uid, exchange_id, encrypted, "ccxt", "LIVE_READY",
                     datetime.now(timezone.utc).isoformat(),
                     json.dumps({"evidence": evidence, "balances": {"USDT": {"free": 10, "used": 0, "total": 10}}})),
                )
            db.commit()
        finally:
            db.close()
        response = self.client.get("/api/v1/live/activation-readiness", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["activationReady"])
        self.assertTrue(payload["crossLiveReady"])
        self.assertEqual(payload["readyCount"], 2)
        self.assertEqual(set(payload["readyExchangeIds"]), {"binance", "bybit"})
        self.assertFalse(payload["liveTradingEnabled"])

    def test_live_engine_requires_live_ready_accounts_before_operator_flag(self):
        self.env.stop()
        self.env = patch.dict(os.environ, {
            "ENGINE_PROXY_TOKEN": "test-service-token",
            "CREDENTIAL_ENCRYPTION_KEY": "ab" * 32,
            "APP_SECURE_COOKIE": "0",
            "ARBX_LIVE_TRADING_ENABLED": "1",
        }, clear=False)
        self.env.start()
        signup = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "activation-gate@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        self.assertEqual(signup.status_code, 200)
        response = self.client.post("/api/v1/engine/start", headers=self.headers, json={
            "mode": "live", "exchange_ids": ["binance", "bybit"], "trade_size_usd": 5,
            "max_loss_usd": 2, "target_profit_usd": 1, "cross_live": True,
            "live_confirmation": "I ACCEPT REAL ORDERS",
        })
        self.assertEqual(response.status_code, 409)
        self.assertIn("LIVE_READY", response.json()["detail"])

    def test_live_activation_readiness_is_fail_closed(self):
        signup = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "activation@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        self.assertEqual(signup.status_code, 200)
        response = self.client.get("/api/v1/live/activation-readiness", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["activationReady"])
        self.assertEqual(payload["readyCount"], 0)
        self.assertEqual(payload["registeredCount"], 18)
        self.assertNotEqual(payload["action"], "ENABLE_OPERATOR_LIVE_FLAG")

