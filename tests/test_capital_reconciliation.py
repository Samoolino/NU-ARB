import tempfile
import unittest
from pathlib import Path

from arbx.journal import TradeJournal


class CapitalReconciliationTests(unittest.TestCase):
    def _journal(self):
        tmp = tempfile.TemporaryDirectory()
        journal = TradeJournal(Path(tmp.name) / "journal.sqlite3")
        self.addCleanup(lambda: journal.close())
        self.addCleanup(tmp.cleanup)
        return journal

    def _execution(self, journal):
        journal.create_execution(
            execution_id="exec-1",
            session_id="session-1",
            mode="live",
            strategy="cross_exchange",
            opportunity_id="opp-1",
            legs=[
                {"leg_index": 0, "exchange_id": "buy", "symbol": "BTC/USDT", "side": "buy", "requested_amount": 1},
                {"leg_index": 1, "exchange_id": "sell", "symbol": "BTC/USDT", "side": "sell", "requested_amount": 1},
            ],
            now=1.0,
        )
        journal.reconcile_leg("exec-1", 0, {"id": "b1", "filled": 1, "cost": 100, "fees": []}, now=2.0)
        journal.reconcile_leg("exec-1", 1, {"id": "s1", "filled": 1, "cost": 101, "fees": []}, now=2.0)

    def test_trade_attributable_movement_is_clean(self):
        journal = self._journal()
        self._execution(journal)
        self.assertEqual(
            journal.execution_expected_deltas("exec-1", "buy"),
            {"BTC": 1.0, "USDT": -100.0},
        )
        journal.record_capital_baseline("session-1", "buy", {"BTC": 0, "USDT": 1000}, now=1.0)
        self.assertTrue(
            journal.reconcile_capital(
                session_id="session-1",
                execution_id="exec-1",
                exchange_id="buy",
                before={"BTC": 0, "USDT": 1000},
                after={"BTC": 1, "USDT": 900},
                expected_deltas={"BTC": 1, "USDT": -100},
                now=3.0,
            )
        )

    def test_unexplained_positive_delta_fails_closed(self):
        journal = self._journal()
        journal.record_capital_baseline("session-1", "buy", {"BTC": 0, "USDT": 1000}, now=1.0)
        clean = journal.reconcile_capital(
            session_id="session-1",
            execution_id="admission-1",
            exchange_id="buy",
            before={"BTC": 0, "USDT": 1000},
            after={"BTC": 0, "USDT": 1050},
            expected_deltas={},
            now=2.0,
        )
        self.assertFalse(clean)
        events = journal.capital_events("session-1")
        self.assertEqual(events[0]["event_type"], "UNKNOWN")
        self.assertEqual(events[0]["amount"], 50.0)


if __name__ == "__main__":
    unittest.main()
