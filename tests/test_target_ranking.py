import unittest

from arbx.ranking import rank_target_progress


class TargetRankingTests(unittest.TestCase):
    def test_target_progress_prefers_expected_verified_progress_over_raw_floor(self):
        best = rank_target_progress(
            expected_net_usd=8.0,
            worst_case_net_usd=6.0,
            age_ms=10.0,
            max_book_age_ms=1000.0,
            capital_utilization=0.10,
            target_remaining_usd=100.0,
        )
        raw_floor_leader = rank_target_progress(
            expected_net_usd=6.5,
            worst_case_net_usd=6.4,
            age_ms=400.0,
            max_book_age_ms=1000.0,
            capital_utilization=0.80,
            target_remaining_usd=100.0,
        )
        self.assertGreater(best.score, raw_floor_leader.score)

    def test_stale_or_concentrated_opportunity_is_discounted(self):
        fresh = rank_target_progress(
            expected_net_usd=5.0,
            worst_case_net_usd=4.0,
            age_ms=10.0,
            max_book_age_ms=1000.0,
            capital_utilization=0.10,
            target_remaining_usd=100.0,
        )
        stale = rank_target_progress(
            expected_net_usd=5.0,
            worst_case_net_usd=4.0,
            age_ms=900.0,
            max_book_age_ms=1000.0,
            capital_utilization=0.90,
            target_remaining_usd=100.0,
        )
        self.assertGreater(fresh.score, stale.score)

    def test_no_target_uses_absolute_expected_progress(self):
        ranked = rank_target_progress(
            expected_net_usd=2.0,
            worst_case_net_usd=1.0,
            age_ms=0.0,
            max_book_age_ms=1000.0,
            capital_utilization=0.0,
            target_remaining_usd=None,
        )
        self.assertEqual(ranked.score, ranked.expected_target_progress)
        self.assertGreater(ranked.score, 0.0)


if __name__ == "__main__":
    unittest.main()
