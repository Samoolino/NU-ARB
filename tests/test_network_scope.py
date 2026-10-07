from arbx.hybrid.networks import (
    MAJOR_NETWORKS,
    VenueSettlementNetwork,
    catalog,
    network_id,
    parse_venue_currency_networks,
)


EXPECTED_NETWORK_IDS = (
    "ethereum",
    "bitcoin",
    "bsc",
    "solana",
    "polygon",
    "arbitrum",
    "optimism",
    "avalanche",
    "base",
    "tron",
    "xrp_ledger",
    "cardano",
    "sui",
    "aptos",
    "near",
    "cosmos",
    "polkadot",
    "litecoin",
    "dogecoin",
    "ton",
)


def test_catalog_has_exact_twenty_identities_and_normalizes_all_aliases():
    assert tuple(catalog()) == EXPECTED_NETWORK_IDS
    assert tuple(network.id for network in MAJOR_NETWORKS) == EXPECTED_NETWORK_IDS
    for network in MAJOR_NETWORKS:
        assert network_id(network.id.upper()) == network.id
        for alias in network.aliases:
            assert network_id(f" {alias.lower()} ") == network.id
    assert network_id("ERC20") == "ethereum"
    assert network_id("TRC20") == "tron"
    assert network_id("SPL") == "solana"
    assert network_id(None) is None
    assert network_id("not-a-catalog-network") is None


def test_structured_venue_report_creates_only_reported_settlement_dimensions():
    discovered = parse_venue_currency_networks(
        " Venue-A ",
        {
            "usdt": {
                "networks": {
                    "TRC20": {"network": "TRON", "deposit": True, "withdraw": True},
                    "ERC20": {"id": "ERC20", "deposit": False, "withdraw": False},
                    "UNLISTED_CHAIN": {"network": "not-in-catalog"},
                }
            },
            "BTC": {"networks": {"BITCOIN": {}}},
        },
    )

    assert discovered == (
        VenueSettlementNetwork("venue-a", "BTC", "bitcoin"),
        VenueSettlementNetwork("venue-a", "USDT", "ethereum"),
        VenueSettlementNetwork("venue-a", "USDT", "tron"),
    )
    for item in discovered:
        assert item.venue_report_status == "VENUE_REPORTED"
        assert item.market_status == "UNVERIFIED"
        assert item.deposit_status == "UNVERIFIED"
        assert item.withdrawal_status == "UNVERIFIED"
        assert item.transfer_status == "UNVERIFIED"
        assert item.execution_route_status == "UNVERIFIED"
        assert not hasattr(item, "order_book")
        assert not hasattr(item, "symbol")


def test_absent_or_malformed_reports_never_promote_catalog_membership_to_venue_evidence():
    malformed_reports = (
        None,
        [],
        "currencies",
        {"USDT": "not structured currency metadata"},
        {"USDT": {"networks": []}},
        {"USDT": {"networks": {"TRC20": "not structured network metadata"}}},
        {"USDT": {"networks": {"TRC20": {"network": 42}}}},
        {"USDT": {"networks": {"TRC20": {"id": "ERC20", "network": "TRC20"}}}},
    )
    for report in malformed_reports:
        assert parse_venue_currency_networks("venue-a", report) == ()
    assert parse_venue_currency_networks("", {"USDT": {"networks": {}}}) == ()


def test_venue_reports_are_isolated_and_do_not_promote_to_transfer_or_execution_routes():
    venue_a = parse_venue_currency_networks(
        "venue-a", {"USDT": {"networks": {"ERC20": {"id": "ERC20"}}}}
    )
    venue_b = parse_venue_currency_networks(
        "venue-b", {"USDT": {"networks": {"TRC20": {"id": "TRC20"}}}}
    )

    assert [(item.venue_id, item.network_id) for item in venue_a] == [
        ("venue-a", "ethereum")
    ]
    assert [(item.venue_id, item.network_id) for item in venue_b] == [
        ("venue-b", "tron")
    ]
    assert venue_a[0].transfer_status == venue_b[0].transfer_status == "UNVERIFIED"
    assert (
        venue_a[0].execution_route_status
        == venue_b[0].execution_route_status
        == "UNVERIFIED"
    )
    assert parse_venue_currency_networks("venue-a", None) == ()
    assert venue_a[0].venue_report_status == "VENUE_REPORTED"
