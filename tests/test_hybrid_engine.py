from arbx.hybrid.registry import VENUE_CATALOG, VENUE_IDS
from arbx.hybrid.depth import normalize_depth, validate_depth
from arbx.hybrid.strategies import profit_compounding_capital, strategy_catalog


def test_catalog_has_twenty_venues():
    expected = {
        "binance", "bybit", "okx", "kucoin", "gateio", "bitget", "kraken",
        "coinbase", "mexc", "htx", "bitfinex", "cryptocom", "coinex",
        "bitstamp", "gemini", "bingx", "lbank", "whitebit", "bitmart", "upbit",
    }
    assert len(VENUE_CATALOG) == 20
    assert set(VENUE_IDS) == expected
    assert len(set(VENUE_IDS)) == 20


def test_catalog_names_are_exact_required_venue_identities():
    expected = {
        "Binance", "Bybit", "OKX", "KuCoin", "Gate.io", "Bitget", "Kraken",
        "Coinbase Exchange", "MEXC", "HTX", "Bitfinex", "Crypto.com Exchange",
        "CoinEx", "Bitstamp", "Gemini", "BingX", "LBank", "WhiteBIT", "BitMart", "Upbit",
    }
    assert {venue.display_name for venue in VENUE_CATALOG} == expected


def test_catalog_membership_does_not_imply_adapter_or_live_capabilities():
    for venue in VENUE_CATALOG:
        assert venue.catalog_status == "CATALOGUED"
        assert venue.spot is False
        assert venue.adapter_status == "UNVERIFIED"
        assert venue.authentication_status == "UNVERIFIED"
        assert venue.market_data_status == "UNVERIFIED"
        assert venue.execution_status == "UNVERIFIED"
        assert venue.live_eligibility_status == "UNVERIFIED"
        assert venue.native_sdk is None


def test_venues_missing_from_control_api_are_explicitly_unavailable():
    by_id = {venue.id: venue for venue in VENUE_CATALOG}
    assert by_id["bitmart"].control_api_status == "UNAVAILABLE"
    assert by_id["upbit"].control_api_status == "UNAVAILABLE"
    assert by_id["bitmart"].engine_selection_supported is False
    assert by_id["upbit"].engine_selection_supported is False
    assert "Not exposed" in by_id["bitmart"].notes
    assert "Not exposed" in by_id["upbit"].notes
    assert {venue.id for venue in VENUE_CATALOG if venue.control_api_status == "AVAILABLE"} == {
        "binance", "bybit", "okx", "kucoin", "gateio", "mexc", "htx", "lbank",
        "bitget", "kraken", "coinbase", "bitfinex", "bitstamp", "gemini",
        "cryptocom", "coinex", "bingx", "whitebit",
    }


def test_catalog_metadata_does_not_make_unavailable_venues_selectable():
    from arbx.hybrid.registry import venue_registry_metadata

    for venue in VENUE_CATALOG:
        metadata = venue_registry_metadata(venue)
        assert metadata["catalogState"] == "CATALOGUED"
        assert metadata["lifecycleState"] == "CATALOGUED"
        assert metadata["engineSelectionAvailable"] is False
        assert metadata["engineSelectionStatus"] == "UNAVAILABLE"


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


def test_live_evidence_requires_every_gate():
    from arbx.hybrid.contracts import VenueEvidence
    evidence = VenueEvidence("x", "ccxt_pro", True, True, True, True, True, True, False, False, ("depth",), {})
    assert not evidence.live_eligible


def test_realized_loss_halts_live_and_profit_compounds():
    from types import SimpleNamespace
    from arbx.gate import RiskManager

    cfg = SimpleNamespace(
        mode="live",
        starter_capital_usd=3.0,
        trade_size_usd=3.0,
        compound_profits=True,
        halt_on_realized_loss=True,
        max_trades_per_min=30,
        max_consecutive_failures=3,
    )
    risk = RiskManager(cfg)
    risk.record(0.25, True)
    assert cfg.trade_size_usd == 3.25
    assert not risk.halted
    risk.record(-0.10, True)
    assert risk.halted
    assert cfg.trade_size_usd == 3.15


def test_certification_journal_round_trip(tmp_path):
    from arbx.journal import TradeJournal

    journal = TradeJournal(tmp_path / "journal.sqlite3")
    journal.record_certification(
        session_id="test",
        venue="binance",
        symbol="BTC/USDT",
        notional_usd=3.0,
        live_eligible=True,
        reasons=(),
        evidence={"rest": {"ok": True}},
    )
    latest = journal.latest_certification("binance")
    journal.close()
    assert latest is not None
    assert latest["live_eligible"] == 1
    assert latest["symbol"] == "BTC/USDT"


def test_capability_route_rejects_missing_ioc():
    from arbx.hybrid.contracts import ExecutionRequirements, VenueCapabilities
    from arbx.hybrid.router import validate_route

    caps = {
        "a": VenueCapabilities(
            spot=True, websocket=True, user_stream=True,
            limit_orders=True, ioc=True,
        ),
        "b": VenueCapabilities(
            spot=True, websocket=True, user_stream=True,
            limit_orders=True, ioc=False,
        ),
    }
    req = ExecutionRequirements(require_ioc=True)
    result = validate_route(caps, ("a", "b"), req)
    assert not result.ok
    assert "b:ioc_required" in result.reasons


def test_pnl_has_expected_and_worst_case_gates():
    from arbx.hybrid.pnl import PnLModel, gate_profit

    model = PnLModel(
        gross_usd=0.20,
        fees_usd=0.04,
        slippage_usd=0.03,
        partial_fill_reserve_usd=0.05,
        safety_reserve_usd=0.02,
    )
    assert round(model.expected_net_usd, 8) == 0.13
    assert round(model.worst_case_net_usd, 8) == 0.06
    ok, reasons = gate_profit(model, 0.01, 0.05)
    assert ok
    assert not reasons


def test_execution_coordinator_tracks_partial_fill():
    from arbx.hybrid.execution import ExecutionCoordinator, ExecutionState

    coordinator = ExecutionCoordinator()
    assert coordinator.on_fill(1.0, 0.6) == ExecutionState.PARTIALLY_FILLED
    assert coordinator.events[-1]["filled"] == 0.6


def test_execution_coordinator_halts_on_negative_pnl():
    from arbx.hybrid.execution import ExecutionCoordinator, ExecutionState

    coordinator = ExecutionCoordinator()
    assert coordinator.gate(expected_net_usd=0.10, worst_case_net_usd=0.02, min_profit_usd=0.01, min_worst_profit_usd=0.01)
    coordinator.on_realized_pnl(-0.01)
    assert coordinator.halted
    assert coordinator.state == ExecutionState.HALTED
