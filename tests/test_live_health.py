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
    samples = [1] * 9

    def stats(self):
        return {"p50": 20.0, "p95": 30.0, "skew": 2.0}

    def ok(self):
        return True


class LiveHealthTests(unittest.TestCase):
    def make_worker(self):
        cfg = SimpleNamespace(mode="paper", depth=10, max_book_age_ms=1000,
                              max_rtt_ms=80, pause_rtt_ms=150)
        worker = ExchangeWorker(ExchangeCfg(id="binance", require_private_stream=True), cfg, None)
        worker.lat = FakeLatency()
        worker.md = SimpleNamespace(
            symbols=["BTC/USDT", "ETH/USDT"],
            books={
                "BTC/USDT": Book([[100.0, 1.0]], [[101.0, 1.0]], time.monotonic(), 123, 456),
            },
        )
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
        self.assertEqual(health["restLatencyMs"]["samples"], 9)
        self.assertTrue(health["restLatencyMs"]["withinLiveThreshold"])
        self.assertEqual(health["publicWebSocket"]["latestBook"]["depthLevels"], {"bids": 1, "asks": 1})
        self.assertEqual(
            health["publicWebSocket"]["latestBook"]["visibleDepthQuote"],
            {"bids": 100.0, "asks": 101.0, "currency": "USDT"},
        )
        self.assertEqual(health["publicWebSocket"]["latestBook"]["spreadBps"], 100.0)
        self.assertEqual(health["orderBookStreams"], {
            "subscribedSymbols": 2, "booksReceived": 1, "freshBooks": 1, "staleBooks": 0,
        })

    def test_health_snapshot_marks_stale_book_and_private_stream(self):
        worker = self.make_worker()
        worker.md.books["BTC/USDT"].recv -= 2
        worker.private_last_message -= 121
        health = worker.health_snapshot()
        self.assertEqual(health["publicWebSocket"]["state"], "STALE")
        self.assertTrue(health["publicWebSocket"]["latestBook"]["stale"])
        self.assertEqual(health["privateWebSocket"]["state"], "STALE")
        self.assertEqual(health["orderBookStreams"]["freshBooks"], 0)
        self.assertEqual(health["orderBookStreams"]["staleBooks"], 1)


if __name__ == "__main__":
    unittest.main()
