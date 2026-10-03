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
        result = asyncio.run(inspect_permissions("bybit", object()))
        self.assertFalse(result["liveEligible"])
        self.assertEqual(result["tradePermission"], "unverified")
