import unittest

from arbx.capital import live_engagement_after_verified_pnl


class LiveEngagementAssuranceTests(unittest.TestCase):
    def test_verified_loss_halts_live_engagement(self):
        self.assertEqual(live_engagement_after_verified_pnl(-0.01), "HALT_LOSS")

    def test_verified_profit_remains_gated(self):
        self.assertEqual(live_engagement_after_verified_pnl(0.01), "CONTINUE_GATED")
        self.assertEqual(live_engagement_after_verified_pnl(0.0), "CONTINUE_GATED")


if __name__ == "__main__":
    unittest.main()
