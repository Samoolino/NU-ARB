from arbx.universe import build_market_universe, select_hot_markets, universe_stats


def test_build_market_universe_excludes_derivatives_and_inactive():
    markets = {
        "BTC/USDT": {"spot": True, "contract": False, "active": True, "base": "BTC", "quote": "USDT"},
        "ETH/USDT": {"spot": True, "contract": False, "active": False, "base": "ETH", "quote": "USDT"},
        "BTC/USDT:USDT": {"spot": False, "contract": True, "active": True, "base": "BTC", "quote": "USDT"},
    }
    universe = build_market_universe(markets, {"BTC/USDT": {"quoteVolume": 1000}})
    assert list(universe) == ["BTC/USDT"]
    assert universe["BTC/USDT"].quote_volume == 1000


def test_hot_selection_is_bounded_but_universe_remains_global():
    markets = {
        f"COIN{i}/USDT": {"spot": True, "contract": False, "active": True, "base": f"COIN{i}", "quote": "USDT"}
        for i in range(5)
    }
    tickers = {f"COIN{i}/USDT": {"quoteVolume": i + 1} for i in range(5)}
    universe = build_market_universe(markets, tickers)
    hot = select_hot_markets(universe, max_symbols=2)
    assert len(universe) == 5
    assert hot == ["COIN4/USDT", "COIN3/USDT"]
    stats = universe_stats(universe, hot)
    assert stats["discoveredMarkets"] == 5
    assert stats["hotMarkets"] == 2
