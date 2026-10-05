import os
import pathlib
import sys
import tempfile
import uuid
import unittest
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx import web_api
from arbx.local_host import LocalHost


class LocalHostTests(unittest.TestCase):
    def setUp(self):
        async def fake_api(scope, receive, send):
            body = b'{"status":"ok"}'
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": body,
            })
            self.forwarded_scope = scope

        self.client = TestClient(LocalHost(fake_api))

    def test_serves_the_local_dashboard(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NU-ARB / TARGET EXECUTION CONSOLE", response.content)
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn(b'id="venues" multiple', response.content)
        self.assertIn(b'id="authForm"', response.content)
        self.assertIn(b'id="liveGuard"', response.content)
        self.assertIn(b'/api/v1/engine/start', response.content)
        self.assertIn(b'/api/v1/engine/events', response.content)

    def test_control_proxy_forwards_only_api_routes_and_adds_local_token(self):
        with patch.dict(os.environ, {"ENGINE_PROXY_TOKEN": "local-test-token"}):
            response = self.client.get("/api/control?path=%2Fapi%2Fv1%2Fruntime")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.forwarded_scope["path"], "/api/v1/runtime")
        self.assertIn(
            (b"x-engine-token", b"local-test-token"),
            self.forwarded_scope["headers"],
        )

    def test_control_proxy_rejects_non_api_paths(self):
        with patch.dict(os.environ, {"ENGINE_PROXY_TOKEN": "local-test-token"}):
            response = self.client.get("/api/control?path=%2Fdocs")

        self.assertEqual(response.status_code, 400)

    def test_public_scanner_validates_registry_before_loading_adapters(self):
        with patch("arbx.public_scanner.scan_public_spot", new_callable=AsyncMock) as scanner:
            response = self.client.get("/api/scan?venues=unknown,binance&symbols=BTC%2FUSDT")

        self.assertEqual(response.status_code, 400)
        scanner.assert_not_awaited()

    def test_public_scanner_routes_bounded_read_only_query(self):
        expected = {
            "executionEnabled": False,
            "dataMode": "public_rest_snapshot",
            "venues": [],
            "opportunities": [],
        }
        with patch("arbx.public_scanner.scan_public_spot", new_callable=AsyncMock, return_value=expected) as scanner:
            response = self.client.get(
                "/api/scan?venues=binance,bybit&symbols=BTC%2FUSDT,ETH%2FUSDC"
                "&notionalUsd=250&takerFeeBps=12&minNetBps=7"
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["executionEnabled"])
        scanner.assert_awaited_once_with(
            ["binance", "bybit"],
            ["BTC/USDT", "ETH/USDC"],
            notional_usd=250.0,
            taker_fee_bps=12.0,
            min_net_bps=7.0,
        )

    def test_public_scanner_rejects_non_get_methods(self):
        response = self.client.post("/api/scan?venues=binance,bybit&symbols=BTC%2FUSDT")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["allow"], "GET")

    def test_local_auth_signup_duplicate_and_login_work_without_echoing_passwords(self):
        original_db = web_api.APP_DB
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "ENGINE_PROXY_TOKEN": "isolated-test-token",
            "CREDENTIAL_ENCRYPTION_KEY": "ab" * 32,
            "APP_SECURE_COOKIE": "0",
        }, clear=False):
            web_api.APP_DB = pathlib.Path(directory) / "local-auth-test.sqlite3"
            client = TestClient(LocalHost(web_api.app))
            email = f"{uuid.uuid4().hex}@example.test"
            password = "test-password-12"
            payload = {
                "email": email,
                "password": password,
                "password_confirmation": password,
            }
            try:
                mismatch = client.post(
                    "/api/control?path=/api/v1/auth/signup",
                    json={**payload, "password_confirmation": "different-test-password"},
                )
                self.assertEqual(mismatch.status_code, 422)
                self.assertEqual(mismatch.json()["detail"], "Passwords do not match")

                signup = client.post("/api/control?path=/api/v1/auth/signup", json=payload)
                self.assertEqual(signup.status_code, 200, signup.text)
                self.assertEqual(client.get(
                    "/api/control?path=/api/v1/auth/me"
                ).json()["user"]["email"], email)

                duplicate = client.post("/api/control?path=/api/v1/auth/signup", json=payload)
                self.assertEqual(duplicate.status_code, 409)
                self.assertIn("already exists", duplicate.json()["detail"])

                client.post("/api/control?path=/api/v1/auth/logout")
                login = client.post("/api/control?path=/api/v1/auth/login", json={
                    "email": email,
                    "password": password,
                })
                self.assertEqual(login.status_code, 200, login.text)
                self.assertEqual(login.json()["user"]["email"], email)
            finally:
                client.close()
                web_api.APP_DB = original_db


if __name__ == "__main__":
    unittest.main()
