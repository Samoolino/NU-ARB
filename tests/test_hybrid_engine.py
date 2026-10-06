from arbx.hybrid.registry import VENUE_CATALOG
from arbx.hybrid.depth import normalize_depth, validate_depth
from arbx.hybrid.strategies import profit_compounding_capital, strategy_catalog

def test_catalog_has_twenty_venues():
    assert len(VENUE_CATALOG) == 20
    assert len({v.id for v in VENUE_CATALOG}) == 20

def test_depth_walk_validation():
    book = normalize_depth("x", "BTC/USDT", {
        "bids": [[100.0, 1.0], [99.9, 1.0], [99.8, 1.0]],
        "asks": [[100.1, 1.0], [100.2, 1.0], [100.3, 1.0]],
        "timestamp": 1,
        "nonce": 7,
    })
    result = validate_depth(book, 100.0, 100000.0)
    assert result.ok
    assert result.top_bid == 100.0
    assert result.top_ask == 100.1

def test_profit_compounding_never_adds_losses():
    assert profit_compounding_capital(3.0, 0.0) == 3.0
    assert profit_compounding_capital(3.0, 0.25) == 3.25
    assert profit_compounding_capital(3.0, -0.25) == 3.0

def test_strategy_catalog():
    assert "triangular_intra_exchange" in strategy_catalog()
    assert "triangular_multi_exchange" in strategy_catalog()
    assert "stablecoin_arbitrage" in strategy_catalog()
