import tempfile
import unittest
from pathlib import Path

from arbx.capital import CapitalDelta, CapitalEventType, classify_unreconciled_delta, target_profit_progress
from arbx.journal import TradeJournal


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

    def test_capital_baseline_and_event_are_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = TradeJournal(Path(tmp) / "journal.db")
            journal.record_capital_baseline("session-1", "binance", {"USDT": {"free": 100}}, now=10.0)
            journal.record_capital_event(
                event_id="evt-1", session_id="session-1", exchange_id="binance",
                asset="USDT", event_type="EXTERNAL_INJECTION", amount=50,
                reference="deposit-1", status="PENDING", now=11.0,
            )
            events = journal.capital_events("session-1")
            self.assertEqual(events[0]["event_type"], "EXTERNAL_INJECTION")
            self.assertEqual(events[0]["amount"], 50)
            baseline = journal.db.execute(
                "SELECT balance_json FROM capital_baselines WHERE session_id=? AND exchange_id=?",
                ("session-1", "binance"),
            ).fetchone()
            self.assertIsNotNone(baseline)
            journal.close()


if __name__ == "__main__":
    unittest.main()
