"""Conservative exchange permission probes with explicit unknown states."""
from __future__ import annotations


async def inspect_permissions(exchange_id: str, exchange) -> dict:
    """Return evidence only when the venue exposes its own authenticated key scopes.

    CCXT's capability flags describe endpoints implemented by the adapter, not the
    permissions granted to a particular API key. Binance exposes a signed,
    read-only API-key restrictions endpoint; the other configured venues remain
    unknown until an equally authoritative adapter probe is implemented.
    """
    if exchange_id != "binance":
        return {
            "source": "no supported authenticated key-scope endpoint",
            "tradePermission": "unverified",
            "withdrawalsDisabled": "unverified",
            "internalTransfersDisabled": "unverified",
            "universalTransfersDisabled": "unverified",
            "ipRestricted": "unverified",
            "liveEligible": False,
        }

    probe = getattr(exchange, "sapiGetAccountApiRestrictions", None)
    if not callable(probe):
        return {
            "source": "Binance API-key restrictions endpoint unavailable in this adapter version",
            "tradePermission": "unverified",
            "withdrawalsDisabled": "unverified",
            "internalTransfersDisabled": "unverified",
            "universalTransfersDisabled": "unverified",
            "ipRestricted": "unverified",
            "liveEligible": False,
        }

    raw = await probe()
    if not isinstance(raw, dict):
        raise RuntimeError("permission endpoint returned an invalid response")

    required = (
        "enableSpotAndMarginTrading",
        "enableWithdrawals",
        "enableInternalTransfer",
        "permitsUniversalTransfer",
        "ipRestrict",
    )
    values = {key: raw.get(key) if isinstance(raw.get(key), bool) else None for key in required}
    trade_ok = values["enableSpotAndMarginTrading"] is True
    withdrawals_off = values["enableWithdrawals"] is False
    internal_off = values["enableInternalTransfer"] is False
    universal_off = values["permitsUniversalTransfer"] is False
    ip_restricted = values["ipRestrict"] is True
    complete = all(value is not None for value in values.values())

    return {
        "source": "Binance GET /sapi/v1/account/apiRestrictions",
        "tradePermission": "enabled" if trade_ok else "disabled" if values["enableSpotAndMarginTrading"] is False else "unverified",
        "withdrawalsDisabled": withdrawals_off,
        "internalTransfersDisabled": internal_off,
        "universalTransfersDisabled": universal_off,
        "ipRestricted": ip_restricted,
        "liveEligible": bool(complete and trade_ok and withdrawals_off and internal_off and universal_off and ip_restricted),
    }
