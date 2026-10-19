import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx.market import cross_timestamp_consistent, validate_stream_order_book


class StreamBookValidationTests(unittest.TestCase):
    def test_rejects_missing_exchange_timestamp(self):
        self.assertEqual(validate_stream_order_book({"bids": [[100, 1]], "asks": [[101, 1]], "timestamp": None}, now_ms=1_800_000_000_000), "missing_exchange_timestamp")

    def test_rejects_stale_and_future_exchange_timestamps(self):
        now = 1_800_000_000_000
        good = {"bids": [[100, 1]], "asks": [[101, 1]]}
        self.assertEqual(validate_stream_order_book({**good, "timestamp": now - 6_000}, now_ms=now), "stale_exchange_timestamp")
        self.assertEqual(validate_stream_order_book({**good, "timestamp": now + 6_000}, now_ms=now), "future_exchange_timestamp")

    def test_rejects_malformed_crossed_or_unsorted_book(self):
        now = 1_800_000_000_000
        self.assertEqual(validate_stream_order_book({"bids": [[100, 1]], "asks": [[99, 1]], "timestamp": now}, now_ms=now), "crossed_order_book")
        self.assertEqual(validate_stream_order_book({"bids": [[100, 1], [101, 1]], "asks": [[102, 1]], "timestamp": now}, now_ms=now), "unsorted_bids")
        self.assertEqual(validate_stream_order_book({"bids": [[100, 1]], "asks": [[101, 0]], "timestamp": now}, now_ms=now), "invalid_level")

    def test_cross_venue_comparison_requires_present_consistent_timestamps(self):
        now = 1_800_000_000_000
        self.assertTrue(cross_timestamp_consistent(now, now + 5_000))
        self.assertFalse(cross_timestamp_consistent(now, now + 5_001))
        self.assertFalse(cross_timestamp_consistent(None, now))
        self.assertFalse(cross_timestamp_consistent("invalid", now))

    def test_accepts_fresh_sorted_book(self):
        now = 1_800_000_000_000
        book = {"bids": [[100, 2], [99, 1]], "asks": [[101, 2], [102, 1]], "timestamp": now}
        self.assertIsNone(validate_stream_order_book(book, now_ms=now))


if __name__ == "__main__":
    unittest.main()

class StreamRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_reinitializes_transport_and_reloads_markets(self):
        from arbx.market import MarketData
        class FakeExchange:
            def __init__(self): self.closed = 0; self.loaded = 0
            async def close(self): self.closed += 1
            async def load_markets(self): self.loaded += 1
        events = []
        exchange = FakeExchange()
        manager = MarketData(exchange, ["BTC/USDT", "ETH/USDT"], 10, lambda *args: events.append(args))
        await manager._recover_connection()
        self.assertEqual((exchange.closed, exchange.loaded), (1, 1))
        await manager._recover_connection()
        self.assertEqual((exchange.closed, exchange.loaded), (1, 1))
        self.assertEqual(manager.max_buffered_symbols, 2)

