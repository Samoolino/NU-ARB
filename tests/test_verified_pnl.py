import tempfile
import unittest
from pathlib import Path

from arbx.journal import TradeJournal


class VerifiedPnlTests(unittest.TestCase):
    def test_live_pnl_requires_verified_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = TradeJournal(Path(tmp) / "journal.sqlite3")
            with self.assertRaises(ValueError):
                journal.record_verified_result(
                    execution_id="missing", session_id="s", mode="live",
                    gross_pnl=11, fees=1, net_pnl=10,
                )
            journal.create_execution(
                execution_id="e1", session_id="s", mode="live",
                strategy="cross_exchange", opportunity_id="o1",
                legs=[
                    {"leg_index": 0, "exchange_id": "a", "symbol": "BTC/USDT", "side": "buy", "requested_amount": 1},
                    {"leg_index": 1, "exchange_id": "b", "symbol": "BTC/USDT", "side": "sell", "requested_amount": 1},
                ],
                now=1,
            )
            journal.transition_execution("e1", "SUBMITTING", now=2)
            journal.transition_execution("e1", "HALTED", now=3, error="test")
            with self.assertRaises(ValueError):
                journal.record_verified_result(
                    execution_id="e1", session_id="s", mode="live",
                    gross_pnl=11, fees=1, net_pnl=10,
                )
            journal.close()

    def test_verified_live_pnl_is_the_realized_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = TradeJournal(Path(tmp) / "journal.sqlite3")
            journal.create_execution(
                execution_id="e1", session_id="s", mode="live",
                strategy="cross_exchange", opportunity_id="o1",
                legs=[
                    {"leg_index": 0, "exchange_id": "a", "symbol": "BTC/USDT", "side": "buy", "requested_amount": 1},
                    {"leg_index": 1, "exchange_id": "b", "symbol": "BTC/USDT", "side": "sell", "requested_amount": 1},
                ],
                now=1,
            )
            journal.transition_execution("e1", "SUBMITTING", now=2)
            journal.transition_execution("e1", "HALTED", now=3, error="test")
            journal.db.execute("UPDATE execution_runs SET state='VERIFIED' WHERE execution_id='e1'")
            journal.db.commit()
            journal.record_verified_result(
                execution_id="e1", session_id="s", mode="live",
                gross_pnl=11, fees=1, net_pnl=10, verified_at=4,
            )
            self.assertEqual(journal.realized("s"), 10)
            journal.close()


if __name__ == "__main__":
    unittest.main()
