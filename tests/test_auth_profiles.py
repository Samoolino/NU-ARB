import pathlib
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx import web_api, worker
from arbx.config import Config, ExchangeCfg
from arbx.hybrid.adapters import ccxt_transport_config
from arbx.hybrid.engine import HybridEngine


class AuthProfileTests(unittest.TestCase):
    def test_binance_auth_profiles_validate_exact_fields_without_echoing_values(self):
        profiles = (
            ("hmac", {"apiKey": " key ", "secret": " secret "},
             {"apiKey": "key", "secret": "secret"}),
            ("rsa", {"apiKey": " key ", "privateKey": " rsa private key "},
             {"apiKey": "key", "privateKey": "rsa private key"}),
            ("ed25519", {"apiKey": " key ", "privateKey": "ed private key"},
             {"apiKey": "key", "privateKey": "ed private key"}),
        )
        for mode, submitted, expected in profiles:
            with self.subTest(mode=mode):
                self.assertEqual(
                    web_api.validate_credentials_for_mode("binance", mode, submitted),
                    expected,
                )

        with self.assertRaises(ValueError) as error:
            web_api.validate_credentials_for_mode(
                "binance", "ed25519", {"apiKey": "key", "secret": "private"}
            )
        self.assertNotIn("private", str(error.exception))

    def test_private_key_profile_is_rejected_outside_binance_rsa_or_ed25519(self):
        for venue_id, auth_mode in (("bybit", "ed25519"), ("binance", "hmac")):
            with self.subTest(venue_id=venue_id, auth_mode=auth_mode), self.assertRaises(ValueError):
                ccxt_transport_config(
                    venue_id, auth_mode,
                    {"apiKey": "key", "privateKey": "private-key"},
                )
        with self.assertRaises(ValueError):
            Config(
                mode="paper",
                exchanges=[ExchangeCfg(
                    id="bybit", auth_mode="ed25519", private_key="private-key",
                )],
            ).validate()

    def test_password_is_required_exactly_when_declared_by_auth_schema(self):
        for exchange_id in ("kucoin", "okx", "bitget", "coinbaseexchange"):
            schema = web_api.AUTH_SCHEMAS[exchange_id]["modes"][0]
            self.assertIn("password", {field["name"] for field in schema["fields"]})
            with self.subTest(exchange_id=exchange_id), self.assertRaises(ValueError):
                web_api.validate_credentials_for_mode(
                    exchange_id, schema["id"], {"apiKey": "key", "secret": "secret"}
                )

        with self.assertRaises(ValueError):
            web_api.validate_credentials_for_mode(
                "bybit", "ccxt",
                {"apiKey": "key", "secret": "secret", "password": "not-in-schema"},
            )

    def test_config_keeps_non_hmac_signing_material_in_private_key_field(self):
        exchange = ExchangeCfg(
            id="binance", api_key="key", private_key="private",
            auth_mode="ed25519",
        )
        config = Config(mode="live", exchanges=[exchange])
        with patch.dict("os.environ", {"BOT_HYBRID_VALIDATION_ONLY": "1"}):
            config.validate()
        self.assertEqual(exchange.secret, "")
        self.assertEqual(exchange.signing_key, "private")

        invalid = Config(
            mode="live",
            exchanges=[ExchangeCfg(id="binance", api_key="key", secret="private",
                                   auth_mode="ed25519")],
        )
        with patch.dict("os.environ", {"BOT_HYBRID_VALIDATION_ONLY": "1"}):
            with self.assertRaisesRegex(ValueError, "PRIVATE_KEY"):
                invalid.validate()

    def test_environment_profiles_do_not_place_private_keys_in_secret_field(self):
        with patch.dict(
            os.environ,
            {
                "BOT_EXCHANGES": "binance",
                "BOT_BINANCE_AUTH_MODE": "ed25519",
                "BOT_BINANCE_KEY": "api-key",
                "BOT_BINANCE_SECRET": "must-not-be-loaded-as-secret",
                "BOT_BINANCE_PRIVATE_KEY": "private-key",
            },
            clear=True,
        ):
            config = Config.from_env()
        exchange = config.exchanges[0]
        self.assertEqual(exchange.api_key, "api-key")
        self.assertEqual(exchange.secret, "")
        self.assertEqual(exchange.private_key, "private-key")

    def test_binance_mapping_happens_at_ccxt_transport_boundary(self):
        captured = []

        class FakeExchange:
            def __init__(self, params):
                captured.append(params)

            async def load_markets(self):
                return {}

        fake_ccxt = types.ModuleType("ccxt")
        fake_pro = types.ModuleType("ccxt.pro")
        fake_pro.binance = FakeExchange
        fake_ccxt.pro = fake_pro
        exchange_cfg = ExchangeCfg(
            id="binance", venue_id="binance", api_key="profile-api-key",
            private_key="profile-private-key", auth_mode="ed25519",
        )
        config = types.SimpleNamespace(exchanges=[exchange_cfg], depth=10)
        adapter = HybridEngine.create(config).adapters["binance"]

        self.assertEqual(exchange_cfg.secret, "")
        self.assertEqual(adapter.credentials, {
            "apiKey": "profile-api-key",
            "privateKey": "profile-private-key",
        })
        with patch.dict(sys.modules, {"ccxt": fake_ccxt, "ccxt.pro": fake_pro}):
            import asyncio
            asyncio.run(adapter.connect())

        self.assertEqual(captured[-1]["apiKey"], "profile-api-key")
        self.assertEqual(captured[-1]["secret"], "profile-private-key")
        self.assertNotIn("privateKey", captured[-1])
        self.assertEqual(adapter.credentials["privateKey"], "profile-private-key")
        self.assertNotIn("secret", adapter.credentials)

    def test_binance_web_and_worker_transport_construction_keep_mapping_local(self):
        captured = []

        class FakeExchange:
            def __init__(self, params):
                captured.append(params)

        fake_ccxt = types.ModuleType("ccxt")
        fake_pro = types.ModuleType("ccxt.pro")
        fake_pro.binance = FakeExchange
        fake_ccxt.pro = fake_pro
        credentials = {"apiKey": "api-key", "privateKey": "private-key"}
        with patch.dict(sys.modules, {"ccxt": fake_ccxt, "ccxt.pro": fake_pro}):
            web_api._make_exchange("binance", "rsa", credentials)
            worker.build_exchange(
                ExchangeCfg(
                    id="binance", venue_id="binance", api_key="api-key",
                    private_key="private-key", auth_mode="rsa",
                ),
                live=False,
            )

        self.assertEqual(credentials, {"apiKey": "api-key", "privateKey": "private-key"})
        for params in captured:
            self.assertEqual(params["apiKey"], "api-key")
            self.assertEqual(params["secret"], "private-key")
            self.assertNotIn("privateKey", params)

    def test_binance_ed25519_pem_signs_without_network_access(self):
        import ccxt.pro as ccxtpro
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private_key = Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        self.assertLessEqual(len(pem), 120, "CCXT 4.5.85 selects EdDSA for short PEM keys")

        transport = ccxt_transport_config(
            "binance", "ed25519", {"apiKey": "test-api-key", "privateKey": pem}
        )
        exchange = ccxtpro.binance(transport)
        signed = exchange.sign(
            "account",
            "private",
            "GET",
            {},
            None,
            None,
        )
        self.assertTrue(signed["url"].startswith("https://"))
        self.assertIsInstance(signed["headers"], dict)
        self.assertNotIn("PRIVATE KEY", signed["url"])
        self.assertEqual(exchange.secret, pem)

    def test_safe_exchange_errors_redact_every_profile_secret(self):
        exchange = types.SimpleNamespace(
            apiKey="sensitive-api-key",
            secret=b"sensitive-signing-material",
            password="sensitive-passphrase",
            privateKey="sensitive-private-key",
        )
        exc = RuntimeError(
            "apiKey=sensitive-api-key; sensitive-signing-material; "
            "sensitive-passphrase; sensitive-private-key"
        )
        message = web_api._safe_exchange_error(exc, exchange)
        for value in (
            "sensitive-api-key",
            "sensitive-signing-material",
            "sensitive-passphrase",
            "sensitive-private-key",
        ):
            self.assertNotIn(value, message)
        self.assertIn("[REDACTED]", message)


if __name__ == "__main__":
    unittest.main()
