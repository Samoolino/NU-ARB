import os
import pathlib
import sys
import unittest
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
        self.assertEqual(len(exchange_ids), 20)
        payload = web_api.EngineStart(
            mode="paper", exchange_ids=exchange_ids, trade_size_usd=5,
            max_loss_usd=2, target_profit_usd=1,
        )
        self.assertEqual(payload.exchange_ids, exchange_ids)
        self.assertIn("bitmart", web_api.VENUES)
        self.assertIn("upbit", web_api.VENUES)
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

    def test_control_plane_store_factory_is_explicit_and_does_not_initialize_sqlite_schema(self):
        from arbx.control_store import ControlPlaneStore

        with patch.dict(os.environ, {
            "ARBX_STORAGE_BACKEND": "sqlite",
            "ARBX_APP_DB": str(self.db_path),
        }, clear=False):
            store = web_api.control_plane_store_from_environment()
        self.assertIsInstance(store, ControlPlaneStore)
        self.assertFalse(self.db_path.exists())

    def test_postgres_configuration_fails_closed_until_routes_are_wired(self):
        self.env.stop()
        self.env = patch.dict(os.environ, {
            "ENGINE_PROXY_TOKEN": "test-service-token",
            "CREDENTIAL_ENCRYPTION_KEY": "ab" * 32,
            "APP_SECURE_COOKIE": "0",
            "ARBX_LIVE_TRADING_ENABLED": "0",
            "ARBX_STORAGE_BACKEND": "postgres",
            "ARBX_DATABASE_URL": "postgresql://localhost/nu_arb_test",
        }, clear=False)
        self.env.start()

        response = self.client.get("/health/ready")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.db_path.exists())

    def test_postgres_auth_routes_fail_closed_without_creating_sqlite_fallback(self):
        self.env.stop()
        self.env = patch.dict(os.environ, {
            "ENGINE_PROXY_TOKEN": "test-service-token",
            "CREDENTIAL_ENCRYPTION_KEY": "ab" * 32,
            "APP_SECURE_COOKIE": "0",
            "ARBX_LIVE_TRADING_ENABLED": "0",
            "ARBX_STORAGE_BACKEND": "postgres",
            "ARBX_DATABASE_URL": "postgresql://localhost/nu_arb_test",
        }, clear=False)
        self.env.start()

        signup = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "postgres-route@example.test", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        login = self.client.post("/api/v1/auth/login", headers=self.headers, json={
            "email": "postgres-route@example.test", "password": "another-long-password",
        })
        me = self.client.get("/api/v1/auth/me", headers=self.headers)
        exchanges = self.client.get("/api/v1/exchanges", headers=self.headers)

        self.assertEqual([signup.status_code, login.status_code, me.status_code, exchanges.status_code],
                         [503, 503, 503, 503])
        for response in (signup, login, me, exchanges):
            self.assertNotIn("postgresql://", response.text)
            self.assertNotIn("password", response.text.lower())
        self.assertFalse(self.db_path.exists())

    def test_session_expiring_at_current_epoch_is_rejected(self):
        signup = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "boundary@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        self.assertEqual(signup.status_code, 200)
        token = self.client.cookies.get(web_api.SESSION_COOKIE)
        self.assertTrue(token)
        digest = web_api.hashlib.sha256(token.encode()).hexdigest()
        db = web_api._connect()
        try:
            db.execute("UPDATE sessions SET expires_at=? WHERE token_hash=?", (int(web_api.time.time()), digest))
            db.commit()
        finally:
            db.close()

        response = self.client.get("/api/v1/auth/me", headers=self.headers)

        self.assertEqual(response.status_code, 401)
        self.assertIn("expired", response.json()["detail"].lower())

    def test_signup_rejects_case_insensitive_duplicate_email(self):
        first = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "Duplicate@Example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        self.assertEqual(first.status_code, 200)

        second = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "duplicate@example.COM", "password": "different-long-password",
            "password_confirmation": "different-long-password",
        })

        self.assertEqual(second.status_code, 409)
        self.assertNotIn("duplicate@example.com", second.text.lower().replace(" ", ""))

    def test_logout_revokes_session_and_clears_cookie(self):
        signup = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "logout@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        self.assertEqual(signup.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=self.headers).status_code, 200)

        logout = self.client.post("/api/v1/auth/logout", headers=self.headers)

        self.assertEqual(logout.status_code, 200)
        self.assertTrue(logout.json()["ok"])
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=self.headers).status_code, 401)

    def test_login_is_case_insensitive_and_rejects_wrong_password(self):
        signup = self.client.post("/api/v1/auth/signup", headers=self.headers, json={
            "email": "LoginCase@example.com", "password": "another-long-password",
            "password_confirmation": "another-long-password",
        })
        self.assertEqual(signup.status_code, 200)
        self.client.post("/api/v1/auth/logout", headers=self.headers)

        wrong = self.client.post("/api/v1/auth/login", headers=self.headers, json={
            "email": "LOGINCASE@example.com", "password": "incorrect-password",
        })
        self.assertEqual(wrong.status_code, 401)

        login = self.client.post("/api/v1/auth/login", headers=self.headers, json={
            "email": "LOGINCASE@example.com", "password": "another-long-password",
        })
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["user"]["email"], "logincase@example.com")
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=self.headers).status_code, 200)

    def test_control_plane_factory_requires_postgres_url_without_falling_back(self):
        with patch.dict(os.environ, {
            "ARBX_STORAGE_BACKEND": "postgres",
            "ARBX_DATABASE_URL": "",
        }, clear=False):
            with self.assertRaisesRegex(ValueError, "ARBX_DATABASE_URL is required"):
                web_api.control_plane_store_from_environment()
        self.assertFalse(self.db_path.exists())

    def test_control_plane_factory_rejects_unknown_backend_without_creating_sqlite(self):
        with patch.dict(os.environ, {
            "ARBX_STORAGE_BACKEND": "mysql",
            "ARBX_DATABASE_URL": "",
        }, clear=False):
            with self.assertRaisesRegex(ValueError, "ARBX_STORAGE_BACKEND must be sqlite or postgres"):
                web_api.control_plane_store_from_environment()
        self.assertFalse(self.db_path.exists())

