from arbx.market import validate_book_snapshot


def test_missing_book_is_rejected():
    assert validate_book_snapshot([], [], 0.0, 250.0) == (False, "missing_levels")


def test_stale_book_is_rejected():
    assert validate_book_snapshot([[100, 1]], [[101, 1]], 251.0, 250.0) == (False, "stale_book")


def test_crossed_and_unsorted_books_are_rejected():
    assert validate_book_snapshot([[101, 1]], [[100, 1]], 1.0, 250.0)[1] == "crossed_book"
    assert validate_book_snapshot([[100, 1], [101, 1]], [[102, 1]], 1.0, 250.0)[1] == "unsorted_bids"


def test_fresh_consistent_book_is_accepted():
    assert validate_book_snapshot([[101, 1], [100, 2]], [[102, 1], [103, 2]], 20.0, 250.0) == (True, "ok")
