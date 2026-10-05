import unittest

from arbx.market import Book


class MarketVolatilityTests(unittest.TestCase):
    def test_book_exposes_volatility_metric(self):
        book = Book(
            bids=[[100.0, 1.0]],
            asks=[[100.1, 1.0]],
            recv=1.0,
            volatility_bps_s=42.5,
        )
        self.assertEqual(book.volatility_bps_s, 42.5)


if __name__ == "__main__":
    unittest.main()
