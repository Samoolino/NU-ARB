import asyncio
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx.permissions import (
    LIVE_PERMISSION_VERIFICATION_VENUES,
    PERMISSION_PROBE_VENUES,
    inspect_permissions,
)


class PermissionProbeTests(unittest.TestCase):
    def test_priority_venue_probe_inventory_is_reported(self):
        self.assertTrue({"bybit", "mexc", "htx", "kucoin", "bitfinex"} <= PERMISSION_PROBE_VENUES)
        self.assertEqual(
            LIVE_PERMISSION_VERIFICATION_VENUES,
            frozenset({"binance", "bybit", "kucoin"}),
        )

    def test_binance_requires_trade_no_withdrawal_no_transfers_and_ip_allowlist(self):
        exchange = type("Exchange", (), {})()
        exchange.sapiGetAccountApiRestrictions = AsyncMock(return_value={
            "enableSpotAndMarginTrading": True,
            "enableWithdrawals": False,
            "enableInternalTransfer": False,
            "permitsUniversalTransfer": False,
            "ipRestrict": True,
        })
        result = asyncio.run(inspect_permissions("binance", exchange))
        self.assertTrue(result["liveEligible"])
        self.assertTrue(result["validationComplete"])
        self.assertEqual(result["tradePermission"], "enabled")
        self.assertTrue(result["withdrawalsDisabled"])

    def test_binance_withdrawals_or_unrestricted_ip_keeps_live_disabled(self):
        exchange = type("Exchange", (), {})()
        exchange.sapiGetAccountApiRestrictions = AsyncMock(return_value={
            "enableSpotAndMarginTrading": True,
            "enableWithdrawals": True,
            "enableInternalTransfer": False,
            "permitsUniversalTransfer": False,
            "ipRestrict": False,
        })
        result = asyncio.run(inspect_permissions("binance", exchange))
        self.assertFalse(result["liveEligible"])

    def test_unknown_exchange_permissions_never_claim_live_eligibility(self):
        for exchange_id in ("bybit", "htx", "mexc", "gateio", "kucoin"):
            with self.subTest(exchange_id=exchange_id):
                result = asyncio.run(inspect_permissions(exchange_id, object()))
                self.assertFalse(result["liveEligible"])
                self.assertEqual(result["tradePermission"], "unverified")

    def test_ip_allowlist_rejects_unrestricted_single_and_combined_ranges(self):
        from arbx.permissions import _ip_restricted

        for allowlist in (
            "0.0.0.0/0",
            ["0.0.0.0/1", "128.0.0.0/1"],
            ["::/1", "8000::/1"],
        ):
            with self.subTest(allowlist=allowlist):
                self.assertFalse(_ip_restricted(allowlist))

    def test_bitfinex_reports_read_only_scope_evidence_without_claiming_spot_eligibility(self):
        exchange = type("Exchange", (), {})()
        exchange.privatePostAuthRPermissions = AsyncMock(return_value=[
            ["orders", 1, 1],
            ["wallets", 1, 0],
            ["withdraw", 0, 0],
            ["funding", 0, 0],
            ["positions", 0, 0],
        ])

        result = asyncio.run(inspect_permissions("bitfinex", exchange))

        self.assertEqual(result["orderWritePermission"], True)
        self.assertEqual(result["withdrawWritePermission"], False)
        self.assertEqual(result["fundingWritePermission"], False)
        self.assertEqual(result["positionsWritePermission"], False)
        self.assertEqual(result["spotAndMarginTradePermission"], "unverified")
        self.assertEqual(result["ipRestricted"], "unverified")
        self.assertFalse(result["liveEligible"])

    def test_bitfinex_rejects_malformed_permission_scopes(self):
        exchange = type("Exchange", (), {})()
        exchange.privatePostAuthRPermissions = AsyncMock(return_value=[["orders", 1, "yes"]])

        result = asyncio.run(inspect_permissions("bitfinex", exchange))
        self.assertFalse(result["validationComplete"])
        self.assertFalse(result["liveEligible"])
        self.assertEqual(result["validationStatus"], "unverified")

    def test_bybit_spot_only_scopes_and_ip_allowlist_can_qualify(self):
        scopes = {name: [] for name in (
            "Spot", "Wallet", "ContractTrade", "Options", "Derivatives",
            "CopyTrading", "BlockTrade", "Exchange", "NFT",
        )}
        scopes["Spot"] = ["SpotTrade"]
        exchange = type("Exchange", (), {})()
        exchange.privateGetV5UserQueryApi = AsyncMock(return_value={
            "retCode": 0,
            "result": {"readOnly": 0, "ips": ["192.0.2.1"], "permissions": scopes},
        })

        result = asyncio.run(inspect_permissions("bybit", exchange))

        self.assertTrue(result["liveEligible"])
        self.assertEqual(result["spotAndMarginTradePermission"], "spot-only")
        self.assertTrue(result["withdrawalsDisabled"])
        self.assertTrue(result["internalTransfersDisabled"])

    def test_bybit_wallet_scope_or_missing_ip_allowlist_blocks_live(self):
        scopes = {name: [] for name in (
            "Spot", "Wallet", "ContractTrade", "Options", "Derivatives",
            "CopyTrading", "BlockTrade", "Exchange", "NFT",
        )}
        scopes["Spot"] = ["SpotTrade"]
        scopes["Wallet"] = ["Withdrawal"]
        exchange = type("Exchange", (), {})()
        exchange.privateGetV5UserQueryApi = AsyncMock(return_value={
            "retCode": 0,
            "result": {"readOnly": 0, "ips": [], "permissions": scopes},
        })

        result = asyncio.run(inspect_permissions("bybit", exchange))

        self.assertFalse(result["liveEligible"])
        self.assertFalse(result["withdrawalsDisabled"])
        self.assertFalse(result["ipRestricted"])

    def test_bybit_requires_success_code_and_strict_read_only_type(self):
        scopes = {name: [] for name in (
            "Spot", "Wallet", "ContractTrade", "Options", "Derivatives",
            "CopyTrading", "BlockTrade", "Exchange", "NFT",
        )}
        scopes["Spot"] = ["SpotTrade"]
        exchange = type("Exchange", (), {})()
        exchange.privateGetV5UserQueryApi = AsyncMock(return_value={
            "retCode": 10010,
            "retMsg": "IP address does not match the allowlist",
            "result": {"readOnly": 0, "ips": ["192.0.2.5"], "permissions": scopes},
        })
        result = asyncio.run(inspect_permissions("bybit", exchange))
        self.assertFalse(result["validationComplete"])
        self.assertFalse(result["liveEligible"])
        self.assertEqual(result["permissionErrorCode"], 10010)
        self.assertIn("IP is not allowlisted", result["source"])
        self.assertNotIn("retMsg", result)

        exchange.privateGetV5UserQueryApi = AsyncMock(return_value={
            "retCode": 0,
            "result": {"readOnly": "0", "ips": ["192.0.2.5"], "permissions": scopes},
        })
        result = asyncio.run(inspect_permissions("bybit", exchange))
        self.assertFalse(result["validationComplete"])
        self.assertFalse(result["liveEligible"])

    def test_kucoin_spot_scope_requires_matching_key_and_ip_allowlist(self):
        exchange = type("Exchange", (), {"apiKey": "test-key"})()
        exchange.privateGetUserApiKey = AsyncMock(return_value={"code": "200000", "data": {
            "apiKey": "test-key", "permission": "General,Spot", "ipWhitelist": "192.0.2.2",
        }})

        result = asyncio.run(inspect_permissions("kucoin", exchange))

        self.assertTrue(result["liveEligible"])
        self.assertEqual(result["validationStatus"], "verified")
        self.assertEqual(result["accountPermissionScopes"], ["General", "Spot"])

    def test_kucoin_broad_permission_scope_blocks_live(self):
        exchange = type("Exchange", (), {"apiKey": "test-key"})()
        exchange.privateGetUserApiKey = AsyncMock(return_value={"code": "200000", "data": {
            "apiKey": "test-key", "permission": "General,Spot,Withdraw", "ipWhitelist": "192.0.2.2",
        }})

        result = asyncio.run(inspect_permissions("kucoin", exchange))

        self.assertFalse(result["liveEligible"])
        self.assertFalse(result["withdrawalsDisabled"])

    def test_kucoin_rejects_non_success_response_even_if_data_looks_valid(self):
        exchange = type("Exchange", (), {"apiKey": "test-key"})()
        exchange.privateGetUserApiKey = AsyncMock(return_value={"code": "400001", "data": {
            "apiKey": "test-key", "permission": "General,Spot", "ipWhitelist": "192.0.2.2",
        }})

        result = asyncio.run(inspect_permissions("kucoin", exchange))

        self.assertFalse(result["validationComplete"])
        self.assertFalse(result["liveEligible"])
        self.assertEqual(result["validationStatus"], "unverified")

    def test_htx_and_mexc_report_complete_probe_but_cannot_claim_live_scope(self):
        htx = type("Exchange", (), {"apiKey": "test-key"})()
        htx.v2PrivateGetUserApiKey = AsyncMock(return_value={"status": "ok", "data": [{
            "accessKey": "test-key", "permission": "readOnly,trade", "ipAddresses": "192.0.2.3",
        }]})
        mexc = type("Exchange", (), {})()
        mexc.spotPrivateGetAccount = AsyncMock(return_value={
            "canTrade": True, "canWithdraw": False, "permissions": ["SPOT"],
        })
        okx = type("Exchange", (), {})()
        okx.privateGetAccountConfig = AsyncMock(return_value={"code": "0", "data": [{
            "perm": "read_only,trade", "ip": "192.0.2.4",
        }]})

        results = [
            asyncio.run(inspect_permissions("htx", htx)),
            asyncio.run(inspect_permissions("mexc", mexc)),
            asyncio.run(inspect_permissions("okx", okx)),
        ]

        self.assertTrue(results[0]["validationComplete"])
        self.assertTrue(results[1]["validationComplete"])
        self.assertTrue(all(result["tradePermission"] == "enabled" for result in results))
        self.assertFalse(results[0]["liveEligible"])
        self.assertFalse(results[1]["liveEligible"])
        self.assertIn("spot_only_trade_scope_not_proven", results[0]["policyBlockers"])
        self.assertIn("ip_allowlist_not_proven", results[1]["policyBlockers"])
        self.assertTrue(all(not result["liveEligible"] for result in results))
        self.assertTrue(results[2]["validationComplete"])
        self.assertIn("spot_only_trade_scope_not_proven", results[2]["policyBlockers"])

    def test_okx_requires_success_envelope_and_bitfinex_reports_complete_scope_shape(self):
        okx = type("Exchange", (), {})()
        okx.privateGetAccountConfig = AsyncMock(return_value={"code": "50000", "data": [{
            "perm": "read_only,trade", "ip": "192.0.2.4",
        }]})
        result = asyncio.run(inspect_permissions("okx", okx))
        self.assertFalse(result["validationComplete"])
        self.assertFalse(result["liveEligible"])

        bitfinex = type("Exchange", (), {})()
        bitfinex.privatePostAuthRPermissions = AsyncMock(return_value=[
            ["orders", 1, 1],
            ["wallets", 1, 0],
            ["withdraw", 0, 0],
            ["funding", 0, 0],
            ["positions", 0, 0],
        ])
        result = asyncio.run(inspect_permissions("bitfinex", bitfinex))
        self.assertTrue(result["validationComplete"])
        self.assertEqual(result["validationStatus"], "verified")
        self.assertFalse(result["liveEligible"])
        self.assertIn("spot_only_trade_scope_not_proven", result["policyBlockers"])

    def test_every_permission_probe_has_uniform_fail_closed_evidence_shape(self):
        for exchange_id in PERMISSION_PROBE_VENUES:
            with self.subTest(exchange_id=exchange_id):
                result = asyncio.run(inspect_permissions(exchange_id, object()))
                for field in (
                    "validationStatus", "validationComplete", "policyBlockers",
                    "tradePermission", "spotAndMarginTradePermission",
                    "withdrawalsDisabled", "internalTransfersDisabled",
                    "universalTransfersDisabled", "ipRestricted", "liveEligible",
                ):
                    self.assertIn(field, result)
                self.assertFalse(result["liveEligible"])
