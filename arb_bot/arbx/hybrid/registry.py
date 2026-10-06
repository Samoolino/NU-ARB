"""Twenty-venue CEX candidate catalog. Membership never implies live eligibility."""
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class VenueSpec:
    id: str
    ccxt_id: str
    adapter: str = "ccxt"
    spot: bool = True
    requires_passphrase: bool = False
    notes: str = ""

VENUE_CATALOG = (
    VenueSpec("binance","binance"),
    VenueSpec("bybit","bybit"),
    VenueSpec("okx","okx",requires_passphrase=True),
    VenueSpec("kucoin","kucoin",requires_passphrase=True),
    VenueSpec("gateio","gate"),
    VenueSpec("bitget","bitget",requires_passphrase=True),
    VenueSpec("kraken","kraken"),
    VenueSpec("coinbase","coinbase"),
    VenueSpec("mexc","mexc"),
    VenueSpec("htx","htx"),
    VenueSpec("bitfinex","bitfinex"),
    VenueSpec("cryptocom","cryptocom"),
    VenueSpec("coinex","coinex"),
    VenueSpec("bitstamp","bitstamp"),
    VenueSpec("gemini","gemini"),
    VenueSpec("bingx","bingx"),
    VenueSpec("lbank","lbank"),
    VenueSpec("whitebit","whitebit"),
    VenueSpec("bitmart","bitmart"),
    VenueSpec("upbit","upbit",notes="Regional/account availability must be verified"),
)
VENUE_IDS = tuple(v.id for v in VENUE_CATALOG)
