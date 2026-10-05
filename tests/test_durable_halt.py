import tempfile
import unittest
from pathlib import Path

from arbx.config import Config
from arbx.gate import RiskManager
from arbx.journal import TradeJournal


class DurableHaltTests(unittest.TestCase):
    def test_halt_survives_risk_manager_restart(self):
        with tempfile.TemporaryDirectory() as td:
            journal = TradeJournal(Path(td) / "journal.sqlite3")
            journal.record_session_halt("s1", "verified live loss")
            restored = journal.session_halt("s1")
            self.assertEqual(restored["reason"], "verified live loss")

            risk = RiskManager(Config())
            if restored:
                risk.halted = True
                risk.reason = restored["reason"]
            self.assertTrue(risk.halted)
            self.assertEqual(risk.reason, "verified live loss")
            journal.close()


if __name__ == "__main__":
    unittest.main()
