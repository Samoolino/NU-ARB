import unittest

from arbx.capital import CapitalDelta, CapitalEventType, classify_unreconciled_delta, target_profit_progress


class CapitalBoundaryTests(unittest.TestCase):
    def test_unreconciled_positive_delta_is_never_profit(self):
        event = classify_unreconciled_delta(CapitalDelta("binance", "USDT", 100, 150, 1.0))
        self.assertEqual(event, CapitalEventType.UNKNOWN)
        self.assertNotEqual(event, "TRADE_RESULT")

    def test_zero_delta_is_dust_adjustment(self):
        self.assertEqual(
            classify_unreconciled_delta(CapitalDelta("binance", "USDT", 100, 100, 1.0)),
            CapitalEventType.ADJUSTMENT_OR_DUST,
        )

    def test_target_progress_uses_realized_trade_pnl_only(self):
        self.assertEqual(target_profit_progress(20, 100), 20.0)
        self.assertEqual(target_profit_progress(20, 100), target_profit_progress(20, 100))


if __name__ == "__main__":
    unittest.main()
