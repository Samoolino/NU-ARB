import pathlib
import sys
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx.config import ExchangeCfg
from arbx.market import Book
from arbx.worker import ExchangeWorker


class FakeLatency:
    def stats(self):
        return {"p50": 20.0, "p95": 30.0, "skew": 2.0}

    def ok(self):
        return True


class LiveHealthTests(unittest.TestCase):
    def make_worker(self):
        cfg = SimpleNamespace(mode="paper", max_book_age_ms=1000)
        worker = ExchangeWorker(ExchangeCfg(id="binance", require_private_stream=True), cfg, None)
        worker.lat = FakeLatency()
        worker.md = SimpleNamespace(books={
            "BTC/USDT": Book([[100.0, 1.0]], [[101.0, 1.0]], time.monotonic(), 123, 456),
        })
        worker.private_stream_ready = True
        worker.private_last_message = time.monotonic()
        return worker

    def test_health_snapshot_reports_received_exchange_book_evidence(self):
        health = self.make_worker().health_snapshot()
        self.assertEqual(health["state"], "HEALTHY")
        self.assertEqual(health["publicWebSocket"]["state"], "LIVE")
        self.assertEqual(health["publicWebSocket"]["latestBook"]["sequence"], 456)
        self.assertEqual(health["privateWebSocket"]["state"], "LIVE")
        self.assertEqual(health["restLatencyMs"]["p95"], 30.0)

    def test_health_snapshot_marks_stale_book_and_private_stream(self):
        worker = self.make_worker()
        worker.md.books["BTC/USDT"].recv -= 2
        worker.private_last_message -= 121
        health = worker.health_snapshot()
        self.assertEqual(health["publicWebSocket"]["state"], "STALE")
        self.assertTrue(health["publicWebSocket"]["latestBook"]["stale"])
        self.assertEqual(health["privateWebSocket"]["state"], "STALE")


if __name__ == "__main__":
    unittest.main()

