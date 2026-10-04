"""Conservative exchange permission probes with explicit unknown states."""
from __future__ import annotations


def _unverified_permissions(source: str) -> dict:
    return {
        "source": source,
        "tradePermission": "unverified",
        "spotAndMarginTradePermission": "unverified",
        "withdrawalsDisabled": "unverified",
        "internalTransfersDisabled": "unverified",
        "universalTransfersDisabled": "unverified",
        "ipRestricted": "unverified",
        "liveEligible": False,
    }


async def inspect_permissions(exchange_id: str, exchange) -> dict:
    """Return evidence only when the venue exposes its own authenticated key scopes.

    CCXT's capability flags describe endpoints implemented by the adapter, not the
    permissions granted to a particular API key. Binance exposes a signed,
    read-only API-key restrictions endpoint. Bitfinex exposes current key scopes,
    but does not distinguish spot orders or prove IP restrictions, so that
    evidence is reported without enabling live trading.
    """
    if exchange_id == "bitfinex":
        probe = getattr(exchange, "privatePostAuthRPermissions", None)
        if not callable(probe):
            return _unverified_permissions(
                "Bitfinex POST /v2/auth/r/permissions unavailable in this adapter version"
            )

        raw = await probe()
        if not isinstance(raw, list):
            raise RuntimeError("Bitfinex permission endpoint returned an invalid response")

        scopes = {}
        for item in raw:
            if (not isinstance(item, (list, tuple)) or len(item) != 3
                    or not isinstance(item[0], str)
                    or item[1] not in (0, 1) or item[2] not in (0, 1)):
                raise RuntimeError("Bitfinex permission endpoint returned an invalid scope")
            scopes[item[0]] = {"read": bool(item[1]), "write": bool(item[2])}

        return {
            **_unverified_permissions("Bitfinex POST /v2/auth/r/permissions"),
            "sourceUrl": "https://docs.bitfinex.com/reference/key-permissions",
            "accountPermissionScopes": scopes,
            "orderWritePermission": scopes.get("orders", {}).get("write", "unverified"),
            "withdrawWritePermission": scopes.get("withdraw", {}).get("write", "unverified"),
            "fundingWritePermission": scopes.get("funding", {}).get("write", "unverified"),
            "positionsWritePermission": scopes.get("positions", {}).get("write", "unverified"),
        }

    if exchange_id != "binance":
        return _unverified_permissions("no supported authenticated key-scope endpoint")

    probe = getattr(exchange, "sapiGetAccountApiRestrictions", None)
    if not callable(probe):
        return _unverified_permissions(
            "Binance API-key restrictions endpoint unavailable in this adapter version"
        )

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
        "sourceUrl": "https://developers.binance.com/en/docs/products/wallet/capital/account/API-key-permission",
        "tradePermission": "enabled" if trade_ok else "disabled" if values["enableSpotAndMarginTrading"] is False else "unverified",
        "spotAndMarginTradePermission": "enabled" if trade_ok else "disabled" if values["enableSpotAndMarginTrading"] is False else "unverified",
        "withdrawalsDisabled": withdrawals_off,
        "internalTransfersDisabled": internal_off,
        "universalTransfersDisabled": universal_off,
        "ipRestricted": ip_restricted,
        "liveEligible": bool(complete and trade_ok and withdrawals_off and internal_off and universal_off and ip_restricted),
    }
