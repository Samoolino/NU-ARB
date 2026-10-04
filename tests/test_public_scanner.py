import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx.public_scanner import rank_snapshot_books, scan_public_spot


class FakeExchange:
    def __init__(self, venue):
        self.venue = venue
        self.markets = {"BTC/USDT": {"spot": True, "contract": False}}
        self.has = {"fetchOrderBook": True}

    async def load_markets(self):
        return self.markets

    async def fetch_order_book(self, symbol, limit):
        price = 100 if self.venue == "binance" else 103
        bid = 99 if self.venue == "binance" else 102
        return {"asks": [[price, 3]], "bids": [[bid, 3]], "timestamp": 1}

    async def close(self):
        pass


class PublicScannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_selected_venues_and_ranks_fully_costed_opportunities(self):
        result = await scan_public_spot(
            ["binance", "bybit"],
            ["BTC/USDT"],
            notional_usd=100,
            taker_fee_bps=10,
            min_net_bps=5,
            exchange_factory=FakeExchange,
        )

        self.assertFalse(result["executionEnabled"])
        self.assertEqual(result["dataMode"], "public_rest_snapshot")
        self.assertEqual(len(result["venues"]), 2)
        self.assertEqual(result["venues"][0]["status"], "available")
        self.assertEqual(result["opportunities"][0]["buyVenue"], "binance")
        self.assertEqual(result["opportunities"][0]["sellVenue"], "bybit")
        self.assertEqual(result["opportunities"][0]["status"], "meets_minimum_estimated_edge")
        self.assertAlmostEqual(result["opportunities"][0]["estimatedFeesUsd"], 0.202)

    async def test_rejects_invalid_inputs_before_opening_exchange_connections(self):
        with self.assertRaisesRegex(ValueError, "spot markets"):
            await scan_public_spot(
                ["binance", "bybit"],
                ["BTCUSDT"],
                notional_usd=100,
                exchange_factory=FakeExchange,
            )

        with self.assertRaisesRegex(ValueError, "Notional"):
            await scan_public_spot(
                ["binance", "bybit"],
                ["BTC/USDT"],
                notional_usd=float("inf"),
                exchange_factory=FakeExchange,
            )

    def test_skips_malformed_book_levels_and_models_both_venue_fees(self):
        result = rank_snapshot_books(
            "BTC/USDT",
            {
                "binance": {"asks": [["invalid", 1], [100, 2]], "bids": [[99, 2]]},
                "bybit": {"asks": [[103, 2]], "bids": [["bad-price", 2], [102, 2]]},
            },
            notional_usd=100,
            taker_fee_bps=10,
            min_net_bps=5,
        )

        self.assertEqual(result[0]["buyVenue"], "binance")
        self.assertEqual(result[0]["sellVenue"], "bybit")
        self.assertFalse(result[0]["executionEnabled"])
        self.assertAlmostEqual(result[0]["netPnlUsd"], 1.798)


if __name__ == "__main__":
    unittest.main()
