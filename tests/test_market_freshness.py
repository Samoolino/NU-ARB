import asyncio
import time
from arbx.market import Book, MarketData, validate_book_snapshot


def test_missing_book_is_rejected():
    assert validate_book_snapshot([], [], 0.0, 250.0) == (False, "missing_levels")


def test_stale_book_is_rejected():
    assert validate_book_snapshot([[100, 1]], [[101, 1]], 251.0, 250.0) == (False, "stale_book")


def test_crossed_and_unsorted_books_are_rejected():
    assert validate_book_snapshot([[101, 1]], [[100, 1]], 1.0, 250.0)[1] == "crossed_book"
    assert validate_book_snapshot([[100, 1], [101, 1]], [[102, 1]], 1.0, 250.0)[1] == "unsorted_bids"


def test_fresh_consistent_book_is_accepted():
    assert validate_book_snapshot([[101, 1], [100, 2]], [[102, 1], [103, 2]], 20.0, 250.0) == (True, "ok")


class FakeExchange:
    id = "binance"
    def __init__(self, book): self.book, self.calls = book, 0
    async def fetch_order_book(self, symbol, limit):
        self.calls += 1
        return self.book


def test_rest_fallback_populates_missing_book_and_signals_scanner():
    ex = FakeExchange({"bids":[[100,2]],"asks":[[101,3]],"timestamp":123,"nonce":9})
    md = MarketData(ex, ["BTC/USDT"], 10, lambda *args: None, 250.0)
    assert asyncio.run(md.refresh_from_rest("BTC/USDT"))
    assert md.books["BTC/USDT"].source == "rest"
    assert "BTC/USDT" in md.dirty and md.event.is_set()


def test_invalid_rest_fallback_does_not_overwrite_last_good_book():
    ex = FakeExchange({"bids":[[102,2]],"asks":[[101,3]]})
    md = MarketData(ex, ["BTC/USDT"], 10, lambda *args: None, 250.0)
    original = Book([[100,2]],[[101,3]],time.monotonic()-1)
    md.books["BTC/USDT"] = original
    assert not asyncio.run(md.refresh_from_rest("BTC/USDT"))
    assert md.books["BTC/USDT"] is original
