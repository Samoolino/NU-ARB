import unittest

from arbx.ranking import rank_target_progress


class SpeedMarginTests(unittest.TestCase):
    def test_fast_completion_gets_higher_confidence(self):
        fast = rank_target_progress(
            expected_net_usd=1.0, worst_case_net_usd=0.8, worst_case_net_bps=8.0, age_ms=10,
            max_book_age_ms=250, capital_utilization=0.1,
            target_remaining_usd=10, volatility_bps_s=20, predicted_completion_ms=20,
            speed_safety_buffer_bps=1,
        )
        slow = rank_target_progress(
            expected_net_usd=1.0, worst_case_net_usd=0.8, age_ms=10,
            max_book_age_ms=250, capital_utilization=0.1,
            target_remaining_usd=10, volatility_bps_s=20, predicted_completion_ms=300,
            speed_safety_buffer_bps=1,
        )
        self.assertGreater(fast.execution_confidence, slow.execution_confidence)
        self.assertGreater(fast.expected_target_progress, slow.expected_target_progress)


if __name__ == "__main__":
    unittest.main()
