import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx.journal import TradeJournal


class OpportunityJournalTests(unittest.TestCase):
    def test_records_opportunity_evidence_across_close_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "engine.sqlite3"
            journal = TradeJournal(path)
            journal.append_opportunity({
                "session_id": "pilot-1",
                "mode": "live",
                "strategy": "cross_exchange",
                "exchange_a": "binance",
                "exchange_b": "bybit",
                "symbol": "BTC/USDT",
                "requested_usd": 10.0,
                "expected_net_usd": 0.05,
                "worst_case_net_usd": 0.02,
                "expected_net_bps": 50.0,
                "worst_case_net_bps": 20.0,
                "book_age_ms": 15.0,
                "decision": "REJECTED_BY_GATE",
                "rejection_reason": "insufficient_inventory",
                "evidence": {"buyBookSequence": 12, "rebalanceHaircutBps": 2.0},
            })
            journal.close()

            reopened = TradeJournal(path)
            try:
                rows = reopened.recent_opportunities()
            finally:
                reopened.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["exchange_a"], "binance")
        self.assertEqual(rows[0]["exchange_b"], "bybit")
        self.assertEqual(rows[0]["decision"], "REJECTED_BY_GATE")
        self.assertEqual(rows[0]["evidence"]["buyBookSequence"], 12)

    def test_opportunity_history_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(pathlib.Path(directory) / "engine.sqlite3")
            try:
                with self.assertRaises(ValueError):
                    journal.recent_opportunities(101)
            finally:
                journal.close()


if __name__ == "__main__":
    unittest.main()
