"""Twenty-venue CEX candidate catalog.

Catalog membership is metadata only. Runtime certification determines whether a
transport and account are actually live eligible.
"""
from dataclasses import dataclass


VENUE_VERIFICATION_STATES = (
    "PUBLIC_MARKET_VERIFIED",
    "PUBLIC_WS_VERIFIED",
    "AUTHENTICATED",
    "BALANCE_VERIFIED",
    "PRIVATE_STREAM_VERIFIED",
    "PERMISSIONS_VERIFIED",
    "EXECUTION_ROUTE_VERIFIED",
    "LIVE_ELIGIBLE",
)


@dataclass(frozen=True, slots=True)
class VenueSpec:
    id: str
    ccxt_id: str
    adapter: str = "ccxt_pro"
    spot: bool = False
    requires_passphrase: bool = False
    native_sdk: str | None = None
    notes: str = ""
    display_name: str = ""
    catalog_status: str = "CATALOGUED"
    adapter_status: str = "UNVERIFIED"
    authentication_status: str = "UNVERIFIED"
    market_data_status: str = "UNVERIFIED"
    execution_status: str = "UNVERIFIED"
    live_eligibility_status: str = "UNVERIFIED"
    control_api_status: str = "UNAVAILABLE"
    control_id: str | None = None
    engine_selection_supported: bool = False


VENUE_CATALOG = (
    VenueSpec("binance", "binance", display_name="Binance", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("bybit", "bybit", display_name="Bybit", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("okx", "okx", requires_passphrase=True, display_name="OKX", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("kucoin", "kucoin", requires_passphrase=True, display_name="KuCoin", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("gateio", "gate", display_name="Gate.io", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("bitget", "bitget", requires_passphrase=True, display_name="Bitget", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("kraken", "kraken", display_name="Kraken", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("coinbase", "coinbase", display_name="Coinbase Exchange", control_api_status="AVAILABLE",
              control_id="coinbaseexchange", engine_selection_supported=True),
    VenueSpec("mexc", "mexc", display_name="MEXC", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("htx", "htx", display_name="HTX", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("bitfinex", "bitfinex", display_name="Bitfinex", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("cryptocom", "cryptocom", display_name="Crypto.com Exchange", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("coinex", "coinex", display_name="CoinEx", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("bitstamp", "bitstamp", display_name="Bitstamp", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("gemini", "gemini", display_name="Gemini", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("bingx", "bingx", display_name="BingX", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("lbank", "lbank", display_name="LBank", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec("whitebit", "whitebit", display_name="WhiteBIT", control_api_status="AVAILABLE", engine_selection_supported=True),
    VenueSpec(
        "bitmart", "bitmart", display_name="BitMart",
        notes="Not exposed in the current web control API; no account verification or scanner selection.",
    ),
    VenueSpec(
        "upbit", "upbit", display_name="Upbit",
        notes="Not exposed in the current web control API; no account verification or scanner selection.",
    ),
)

VENUE_IDS = tuple(v.id for v in VENUE_CATALOG)


def venue_registry_metadata(
    venue: VenueSpec,
    *,
    evidence: dict | None = None,
    evidence_fresh: bool = False,
    engine_selection_available: bool = False,
) -> dict:
    """Expose catalog identity separately from fresh runtime verification evidence."""
    evidence = evidence if evidence_fresh and isinstance(evidence, dict) else {}
    capabilities = evidence.get("executionCapabilities") or {}
    permissions = evidence.get("permissions") or {}
    permission_status = evidence.get("tradePermission")
    states = {
        "PUBLIC_MARKET_VERIFIED": bool(
            evidence.get("publicMarket") is True
            or (evidence.get("rest") is True and capabilities.get("spotMarket") is True)
        ),
        "PUBLIC_WS_VERIFIED": evidence.get("publicWebSocket") is True,
        "AUTHENTICATED": bool(evidence.get("authentication")),
        "BALANCE_VERIFIED": bool(evidence.get("balances")),
        "PRIVATE_STREAM_VERIFIED": bool(evidence.get("privateWebSocket")),
        "PERMISSIONS_VERIFIED": bool(
            permissions.get("source")
            and permission_status in ("enabled", "disabled")
        ),
        # The control preflight checks adapter declarations, not an actual order route.
        "EXECUTION_ROUTE_VERIFIED": False,
        "LIVE_ELIGIBLE": bool(evidence.get("liveEligible")),
    }
    lifecycle_state = "CATALOGUED"
    for state in VENUE_VERIFICATION_STATES:
        if not states[state]:
            break
        lifecycle_state = state
    return {
        "catalogId": venue.id,
        "controlId": venue.control_id or venue.id,
        "catalogState": venue.catalog_status,
        "lifecycleState": lifecycle_state,
        "verificationStates": states,
        "engineSelectionSupported": venue.engine_selection_supported,
        "engineSelectionAvailable": bool(
            venue.engine_selection_supported and engine_selection_available
        ),
        "engineSelectionStatus": (
            "AVAILABLE"
            if venue.engine_selection_supported and engine_selection_available
            else "UNAVAILABLE"
        ),
        "controlApiStatus": venue.control_api_status,
    }
