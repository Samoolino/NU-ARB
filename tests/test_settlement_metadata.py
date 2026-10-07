import sqlite3

import pytest

from arbx.hybrid.settlement_metadata import (
    SettlementMetadataRepository,
    VenueSettlementMetadata,
    canonical_asset,
    parse_venue_settlement_metadata,
)


def test_supported_asset_and_network_aliases_are_normalized_without_expanding_scope():
    assert canonical_asset(" matic ") == "POL"
    assert canonical_asset("pol") == "POL"
    assert canonical_asset("DOGE") is None

    parsed = parse_venue_settlement_metadata(
        " Venue-A ",
        {
            "MATIC": {"networks": {"POLYGON": {}}},
            "POL": {"networks": {"id": {"id": "POLYGON"}}},
            "USDT": {"networks": {"TRC20": {"network": "TRON"}}},
            "DOGE": {"networks": {"DOGE": {"id": "DOGE"}}},
        },
    )
    assert [(item.venue_id, item.asset, item.network_id) for item in parsed] == [
        ("venue-a", "POL", "polygon"),
        ("venue-a", "USDT", "tron"),
    ]


def test_api_report_fields_are_retained_and_not_inferred():
    parsed = parse_venue_settlement_metadata(
        "venue-a",
        {
            "USDT": {
                "networks": {
                    "TRC20": {
                        "network": "TRON",
                        "deposit": True,
                        "withdraw": False,
                        "fee": "1.25",
                        "limits": {"withdraw": {"min": "5"}},
                        "confirmations": "19",
                        "contract": "  TRON-CONTRACT-1  ",
                    }
                }
            }
        },
    )
    assert parsed == (
        VenueSettlementMetadata(
            venue_id="venue-a",
            asset="USDT",
            network_id="tron",
            deposit_enabled=True,
            withdrawal_enabled=False,
            fee=1.25,
            minimum=5.0,
            confirmations=19,
            contract_address="TRON-CONTRACT-1",
        ),
    )

    sparse = parse_venue_settlement_metadata(
        "venue-a", {"BTC": {"networks": {"BITCOIN": {"chain": "BTC"}}}}
    )[0]
    assert sparse.deposit_enabled is None
    assert sparse.withdrawal_enabled is None
    assert sparse.fee is None
    assert sparse.minimum is None
    assert sparse.confirmations is None
    assert sparse.contract_address is None


def test_disabled_and_unknown_transfer_metadata_never_enable_operations():
    parsed = parse_venue_settlement_metadata(
        "venue-a",
        {
            "USDC": {
                "networks": {
                    "ERC20": {"deposit": False, "withdraw": False},
                    "SOL": {},
                }
            }
        },
    )
    disabled = next(item for item in parsed if item.network_id == "ethereum")
    unknown = next(item for item in parsed if item.network_id == "solana")

    assert disabled.deposit_enabled is False
    assert disabled.withdrawal_enabled is False
    assert unknown.deposit_enabled is None
    assert unknown.withdrawal_enabled is None
    for item in parsed:
        assert item.transfer_status == "UNVERIFIED"
        assert item.withdrawal_operation_enabled is False
        assert item.transfer_operation_enabled is False
        assert not hasattr(item, "submit_withdrawal")
        assert not hasattr(item, "submit_transfer")


def test_malformed_or_conflicting_metadata_values_are_unknown_and_bad_networks_rejected():
    parsed = parse_venue_settlement_metadata(
        "venue-a",
        {
            "ETH": {
                "networks": {
                    "ERC20": {
                        "id": "ETHEREUM",
                        "deposit": "true",
                        "withdraw": False,
                        "withdrawal_enabled": True,
                        "fee": -1,
                        "minimum": "not-a-number",
                        "confirmations": 1.5,
                        "contractAddress": "",
                    },
                    "BAD": {"id": "ethereum", "network": "tron"},
                }
            }
        },
    )
    assert len(parsed) == 1
    row = parsed[0]
    assert row.network_id == "ethereum"
    assert row.deposit_enabled is None
    assert row.withdrawal_enabled is None
    assert row.fee is None
    assert row.minimum is None
    assert row.confirmations is None
    assert row.contract_address is None


def test_conflicting_duplicate_asset_network_rows_do_not_choose_by_iteration_order():
    parsed = parse_venue_settlement_metadata(
        "venue-a",
        {
            "MATIC": {
                "networks": {
                    "POLYGON": {"deposit": True, "fee": "0.1"},
                }
            },
            "POL": {
                "networks": {
                    "POL": {"deposit": False, "fee": "0.2", "withdraw": True},
                }
            },
        },
    )
    assert parsed == (
        VenueSettlementMetadata(
            venue_id="venue-a",
            asset="POL",
            network_id="polygon",
            deposit_enabled=None,
            withdrawal_enabled=True,
            fee=None,
        ),
    )


def test_isolated_sqlite_repository_upserts_and_reads_only_exact_venue_dimensions():
    connection = sqlite3.connect(":memory:")
    repository = SettlementMetadataRepository(connection)
    first = VenueSettlementMetadata(
        "venue-a", "USDT", "tron", False, True, 1.0, 2.0, 10, "contract-a"
    )
    repository.upsert(first)
    repository.upsert(VenueSettlementMetadata("venue-b", "USDT", "tron", True))
    repository.upsert(VenueSettlementMetadata("venue-a", "USDC", "ethereum", True))

    replacement = VenueSettlementMetadata("venue-a", "USDT", "tron")
    repository.upsert(replacement)

    assert repository.get("VENUE-A", "USDT", "TRC20") == replacement
    assert repository.get("venue-b", "USDT", "TRC20") == VenueSettlementMetadata(
        "venue-b", "USDT", "tron", deposit_enabled=True
    )
    assert repository.get("venue-a", "USDT", "ethereum") is None
    assert repository.list_for_venue("venue-a") == (
        VenueSettlementMetadata("venue-a", "USDC", "ethereum", deposit_enabled=True),
        replacement,
    )
    assert repository.list_for_venue("venue-b") == (
        VenueSettlementMetadata("venue-b", "USDT", "tron", deposit_enabled=True),
    )
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert tables == {"venue_settlement_metadata"}
    connection.close()


def test_repository_rejects_invalid_rows_and_batch_upsert_is_atomic():
    connection = sqlite3.connect(":memory:")
    repository = SettlementMetadataRepository(connection)
    good = VenueSettlementMetadata("venue-a", "BTC", "bitcoin")
    malformed = VenueSettlementMetadata("venue-a", "DOGE", "bitcoin")

    with pytest.raises(ValueError):
        repository.upsert(malformed)
    with pytest.raises(ValueError):
        repository.upsert_many((good, malformed))
    assert repository.get("venue-a", "BTC", "bitcoin") is None
    connection.close()


def test_absent_or_unsupported_reports_never_fabricate_asset_network_or_cost_metadata():
    for report in (
        None,
        [],
        {"USDT": "not currency metadata"},
        {"USDT": {"networks": []}},
        {"USDT": {"networks": {"UNKNOWN": {"fee": 0, "deposit": True}}}},
    ):
        assert parse_venue_settlement_metadata("venue-a", report) == ()
    assert parse_venue_settlement_metadata("", {"USDT": {"networks": {}}}) == ()
