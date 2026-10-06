"""Major settlement-network registry used by the opportunity scanner.

Networks are discovery/cost-routing dimensions, not substitutes for exchange
connectivity. A network is considered live-verified only after an exchange
adapter reports the asset/network as available; this registry alone never
claims a chain is reachable or transferable.
"""

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
    SettlementNetwork("polygon", "evm", "POL", ("MATIC", "POLYGON")),
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
    alias.upper(): network.id
    for network in MAJOR_NETWORKS
    for alias in network.aliases
}


def network_id(value: str) -> str | None:
    key = value.strip().upper()
    if key in NETWORK_BY_ID:
        return key.lower()
    return NETWORK_ALIASES.get(key)


def catalog() -> list[str]:
    return [network.id for network in MAJOR_NETWORKS]
