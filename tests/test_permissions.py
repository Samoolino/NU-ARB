import asyncio
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx.permissions import inspect_permissions


class PermissionProbeTests(unittest.TestCase):
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

        with self.assertRaisesRegex(RuntimeError, "invalid scope"):
            asyncio.run(inspect_permissions("bitfinex", exchange))
