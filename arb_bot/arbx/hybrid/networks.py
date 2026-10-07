"""Canonical settlement-network catalog and metadata-only discovery parser.

Networks are settlement dimensions, never CEX markets or order books. Catalog
membership is not venue evidence. ``VENUE_REPORTED`` is emitted only when the
caller supplies structured currency/network metadata; market, deposit,
withdrawal, transfer, and execution-route evidence remain independently
unverified. This module performs no I/O.
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SettlementNetwork:
    id: str
    family: str
    native_asset: str
    aliases: tuple[str, ...] = ()


MAJOR_NETWORKS: tuple[SettlementNetwork, ...] = (
    SettlementNetwork("ethereum", "evm", "ETH", ("ERC20", "ETHEREUM")),
    SettlementNetwork("bitcoin", "utxo", "BTC", ("BTC", "BITCOIN")),
    SettlementNetwork("bsc", "evm", "BNB", ("BEP20", "BSC")),
    SettlementNetwork("solana", "solana", "SOL", ("SOL", "SPL")),
    SettlementNetwork("polygon", "evm", "POL", ("MATIC", "POL", "POLYGON")),
    SettlementNetwork("arbitrum", "evm", "ETH", ("ARBITRUM", "ARB")),
    SettlementNetwork("optimism", "evm", "ETH", ("OPTIMISM", "OP")),
    SettlementNetwork("avalanche", "evm", "AVAX", ("AVAXC", "CCHAIN")),
    SettlementNetwork("base", "evm", "ETH", ("BASE",)),
    SettlementNetwork("tron", "tron", "TRX", ("TRC20", "TRON")),
    SettlementNetwork("xrp_ledger", "xrpl", "XRP", ("XRPL",)),
    SettlementNetwork("cardano", "cardano", "ADA", ("ADA",)),
    SettlementNetwork("sui", "sui", "SUI", ("SUI",)),
    SettlementNetwork("aptos", "aptos", "APT", ("APT",)),
    SettlementNetwork("near", "near", "NEAR", ("NEAR",)),
    SettlementNetwork("cosmos", "cosmos", "ATOM", ("COSMOS", "IBC")),
    SettlementNetwork("polkadot", "substrate", "DOT", ("DOT",)),
    SettlementNetwork("litecoin", "utxo", "LTC", ("LTC",)),
    SettlementNetwork("dogecoin", "utxo", "DOGE", ("DOGE",)),
    SettlementNetwork("ton", "ton", "TON", ("TONCOIN",)),
)

NETWORK_BY_ID = {network.id: network for network in MAJOR_NETWORKS}
NETWORK_ALIASES = {
    alias.strip().casefold(): network.id
    for network in MAJOR_NETWORKS
    for alias in (network.id, *network.aliases)
}


def network_id(value: object) -> str | None:
    """Return the catalog identity for an ID or known alias; unknowns stay unknown."""
    if not isinstance(value, str):
        return None
    return NETWORK_ALIASES.get(value.strip().casefold())


def catalog() -> list[str]:
    return [network.id for network in MAJOR_NETWORKS]


@dataclass(frozen=True, slots=True)
class VenueSettlementNetwork:
    """One venue-reported asset/network dimension, without market or route claims."""

    venue_id: str
    asset: str
    network_id: str

    @property
    def venue_report_status(self) -> str:
        return "VENUE_REPORTED"

    @property
    def market_status(self) -> str:
        return "UNVERIFIED"

    @property
    def deposit_status(self) -> str:
        return "UNVERIFIED"

    @property
    def withdrawal_status(self) -> str:
        return "UNVERIFIED"

    @property
    def transfer_status(self) -> str:
        return "UNVERIFIED"

    @property
    def execution_route_status(self) -> str:
        return "UNVERIFIED"


def parse_venue_currency_networks(
    venue_id: str,
    currency_report: object,
) -> tuple[VenueSettlementNetwork, ...]:
    """Normalize structured venue currency metadata into settlement dimensions.

    The accepted shape is a currency mapping whose entries contain a ``networks``
    mapping, e.g. ``{"USDT": {"networks": {"TRC20": {"id": "TRC20"}}}}``.
    A network must be explicitly present in that report and normalize to one of
    the fixed catalog identities. Catalog entries are never emitted as venue
    observations by themselves. Malformed entries are ignored without inferring
    status from deposit/withdrawal flags.
    """
    if not isinstance(venue_id, str) or not venue_id.strip():
        return ()
    if not isinstance(currency_report, Mapping):
        return ()

    normalized_venue = venue_id.strip().casefold()
    observations: set[tuple[str, str]] = set()
    network_fields = ("network", "networkId", "id", "chain")

    for raw_asset, currency in currency_report.items():
        if not isinstance(raw_asset, str) or not raw_asset.strip():
            continue
        if not isinstance(currency, Mapping):
            continue
        raw_networks = currency.get("networks")
        if not isinstance(raw_networks, Mapping):
            continue

        asset = raw_asset.strip().upper()
        for raw_key, metadata in raw_networks.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                continue
            if not isinstance(metadata, Mapping):
                continue

            explicit_fields = [field for field in network_fields if field in metadata]
            if explicit_fields:
                explicit_ids = []
                for field in explicit_fields:
                    raw_value = metadata[field]
                    if not isinstance(raw_value, str) or not raw_value.strip():
                        explicit_ids = []
                        break
                    canonical = network_id(raw_value)
                    if canonical is None:
                        explicit_ids = []
                        break
                    explicit_ids.append(canonical)
                if not explicit_ids or len(set(explicit_ids)) != 1:
                    continue
                canonical_network = explicit_ids[0]
            else:
                canonical_network = network_id(raw_key)
                if canonical_network is None:
                    continue

            observations.add((asset, canonical_network))

    return tuple(
        VenueSettlementNetwork(normalized_venue, asset, canonical_network)
        for asset, canonical_network in sorted(observations)
    )
