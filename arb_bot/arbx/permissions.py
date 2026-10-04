"""Conservative exchange permission probes with explicit unknown states."""
from __future__ import annotations

import ipaddress


PERMISSION_PROBE_VENUES = frozenset(
    {"binance", "bybit", "kucoin", "htx", "mexc", "okx", "bitfinex"}
)
LIVE_PERMISSION_VERIFICATION_VENUES = frozenset({"binance", "bybit", "kucoin"})


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


def _ip_restricted(value) -> bool | str:
    if isinstance(value, list):
        entries = value
    if isinstance(value, str):
        entries = [item.strip() for item in value.split(",") if item.strip()]
    elif not isinstance(value, list):
        return "unverified"
    if not entries:
        return False
    if not all(isinstance(item, str) and item.strip() for item in entries):
        return "unverified"
    try:
        networks = [ipaddress.ip_network(item.strip(), strict=False) for item in entries]
    except ValueError:
        return "unverified"
    return all(network.prefixlen > 0 for network in networks)


async def inspect_permissions(exchange_id: str, exchange) -> dict:
    """Return evidence only when the venue exposes its own authenticated key scopes.

    CCXT's capability flags describe endpoints implemented by the adapter, not the
    permissions granted to a particular API key. A live-eligible result requires
    explicit trade scopes, disabled withdrawal/transfer capabilities, and an IP
    allowlist. Evidence that cannot prove all three remains ineligible.
    """
    if exchange_id == "binance":
        probe = getattr(exchange, "sapiGetAccountApiRestrictions", None)
        if not callable(probe):
            return _unverified_permissions(
                "Binance GET /sapi/v1/account/apiRestrictions unavailable in this adapter version"
            )
        raw = await probe()
        if not isinstance(raw, dict):
            raise RuntimeError("Binance permission endpoint returned an invalid response")

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

    if exchange_id == "bybit":
        probe = getattr(exchange, "privateGetV5UserQueryApi", None)
        if not callable(probe):
            return _unverified_permissions(
                "Bybit GET /v5/user/query-api unavailable in this adapter version"
            )
        raw = await probe()
        result = raw.get("result") if isinstance(raw, dict) else None
        scopes = result.get("permissions") if isinstance(result, dict) else None
        scope_names = ("Spot", "Wallet", "ContractTrade", "Options", "Derivatives",
                       "CopyTrading", "BlockTrade", "Exchange", "NFT")
        if (not isinstance(scopes, dict) or any(
                name not in scopes or not isinstance(scopes[name], list)
                or not all(isinstance(scope, str) for scope in scopes[name])
                for name in scope_names)
                or any(not isinstance(values, list) or not all(isinstance(scope, str) for scope in values)
                       for values in scopes.values())):
            return _unverified_permissions("Bybit GET /v5/user/query-api returned incomplete permission scopes")

        spot_scopes = set(scopes["Spot"])
        wallet_scopes = set(scopes["Wallet"])
        active_non_spot = {name: values for name, values in scopes.items()
                           if name != "Spot" and isinstance(values, list) and values}
        read_only = result.get("readOnly")
        ip_restricted = _ip_restricted(result.get("ips"))
        trade_enabled = read_only in (0, False) and "SpotTrade" in spot_scopes
        withdrawals_disabled = not bool(wallet_scopes & {"Withdrawal", "Withdraw"})
        transfers_disabled = not bool(wallet_scopes & {
            "AccountTransfer", "SubMemberTransfer", "SubaccountTransfer", "UniversalTransfer"
        })
        complete = isinstance(read_only, (int, bool)) and isinstance(result.get("ips"), list)
        return {
            "source": "Bybit GET /v5/user/query-api",
            "sourceUrl": "https://bybit-exchange.github.io/docs/v5/user/query-api",
            "accountPermissionScopes": {name: list(values) for name, values in scopes.items()
                                        if isinstance(values, list)},
            "tradePermission": "enabled" if trade_enabled else "disabled" if "SpotTrade" not in spot_scopes else "unverified",
            "spotAndMarginTradePermission": "spot-only" if trade_enabled and not active_non_spot else "unverified",
            "withdrawalsDisabled": withdrawals_disabled,
            "internalTransfersDisabled": transfers_disabled,
            "universalTransfersDisabled": transfers_disabled,
            "ipRestricted": ip_restricted,
            "liveEligible": bool(complete and trade_enabled and not active_non_spot
                                 and withdrawals_disabled and transfers_disabled and ip_restricted is True),
        }

    if exchange_id == "kucoin":
        probe = getattr(exchange, "privateGetUserApiKey", None)
        if not callable(probe):
            return _unverified_permissions(
                "KuCoin GET /api/v1/user/api-key unavailable in this adapter version"
            )
        raw = await probe()
        data = raw.get("data") if isinstance(raw, dict) else None
        if (not isinstance(data, dict) or data.get("apiKey") != getattr(exchange, "apiKey", None)):
            return _unverified_permissions("KuCoin key-info response did not identify the configured API key")
        raw_permissions = data.get("permission")
        if isinstance(raw_permissions, str):
            permissions = {item.strip() for item in raw_permissions.split(",") if item.strip()}
        elif isinstance(raw_permissions, list) and all(isinstance(item, str) for item in raw_permissions):
            permissions = set(raw_permissions)
        else:
            return _unverified_permissions("KuCoin key-info response omitted parseable permission scopes")

        ip_restricted = _ip_restricted(data.get("ipWhitelist"))
        spot_trade = "Spot" in permissions
        scope_safe = permissions <= {"General", "Spot"}
        withdrawals_disabled = "Withdraw" not in permissions and "Withdrawal" not in permissions
        transfers_disabled = "Transfer" not in permissions and "AccountTransfer" not in permissions
        return {
            "source": "KuCoin GET /api/v1/user/api-key",
            "sourceUrl": "https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-api-key-info",
            "accountPermissionScopes": sorted(permissions),
            "tradePermission": "enabled" if spot_trade else "disabled",
            "spotAndMarginTradePermission": "spot-only" if spot_trade and scope_safe else "unverified",
            "withdrawalsDisabled": withdrawals_disabled,
            "internalTransfersDisabled": transfers_disabled,
            "universalTransfersDisabled": transfers_disabled,
            "ipRestricted": ip_restricted,
            "liveEligible": bool(spot_trade and scope_safe and withdrawals_disabled
                                 and transfers_disabled and ip_restricted is True),
        }

    if exchange_id == "htx":
        probe = getattr(exchange, "v2PrivateGetUserApiKey", None) or getattr(exchange, "privateGetUserApiKey", None)
        if not callable(probe):
            return _unverified_permissions("HTX API-key info endpoint unavailable in this adapter version")
        raw = await probe()
        records = raw.get("data") if isinstance(raw, dict) else None
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            return _unverified_permissions("HTX API-key info response did not contain key records")
        record = next((item for item in records if isinstance(item, dict)
                       and item.get("accessKey") == getattr(exchange, "apiKey", None)), None)
        if record is None:
            return _unverified_permissions("HTX API-key info response did not identify the configured API key")
        raw_scopes = record.get("permission")
        scopes = ({item.strip().lower() for item in raw_scopes.split(",") if item.strip()}
                  if isinstance(raw_scopes, str) else
                  {item.lower() for item in raw_scopes if isinstance(item, str)}
                  if isinstance(raw_scopes, list) else set())
        if not scopes:
            return _unverified_permissions("HTX API-key info response omitted permission scopes")
        ip_restricted = _ip_restricted(
            record.get("ipAddresses", record.get("ipWhitelist", record.get("ip")))
        )
        trade_enabled = "trade" in scopes
        withdrawals_disabled = not bool(scopes & {"withdraw", "withdrawal"})
        return {
            "source": "HTX API-key info endpoint",
            "accountPermissionScopes": sorted(scopes),
            "tradePermission": "enabled" if trade_enabled else "disabled",
            "spotAndMarginTradePermission": "unverified",
            "withdrawalsDisabled": withdrawals_disabled,
            "internalTransfersDisabled": "unverified",
            "universalTransfersDisabled": "unverified",
            "ipRestricted": ip_restricted,
            "liveEligible": False,
        }

    if exchange_id == "mexc":
        probe = getattr(exchange, "spotPrivateGetAccount", None)
        if not callable(probe):
            return _unverified_permissions("MEXC GET /api/v3/account unavailable in this adapter version")
        raw = await probe()
        permissions = raw.get("permissions") if isinstance(raw, dict) else None
        can_trade = raw.get("canTrade") if isinstance(raw, dict) else None
        can_withdraw = raw.get("canWithdraw") if isinstance(raw, dict) else None
        if not isinstance(permissions, list) or not all(isinstance(item, str) for item in permissions):
            return _unverified_permissions("MEXC account response omitted permission scopes")
        return {
            "source": "MEXC GET /api/v3/account",
            "accountPermissionScopes": permissions,
            "tradePermission": "enabled" if can_trade is True else "disabled" if can_trade is False else "unverified",
            "spotAndMarginTradePermission": "spot-only" if permissions == ["SPOT"] else "unverified",
            "withdrawalsDisabled": can_withdraw is False,
            "internalTransfersDisabled": "unverified",
            "universalTransfersDisabled": "unverified",
            "ipRestricted": "unverified",
            "liveEligible": False,
        }

    if exchange_id == "okx":
        probe = getattr(exchange, "privateGetAccountConfig", None)
        if not callable(probe):
            return _unverified_permissions("OKX GET /api/v5/account/config unavailable in this adapter version")
        raw = await probe()
        data = raw.get("data") if isinstance(raw, dict) else None
        record = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None
        if record is None or not isinstance(record.get("perm"), str):
            return _unverified_permissions("OKX account config omitted API-key permission scopes")
        scopes = {item.strip().lower() for item in record["perm"].split(",") if item.strip()}
        return {
            "source": "OKX GET /api/v5/account/config",
            "accountPermissionScopes": sorted(scopes),
            "tradePermission": "enabled" if "trade" in scopes else "disabled",
            "spotAndMarginTradePermission": "unverified",
            "withdrawalsDisabled": "withdraw" not in scopes,
            "internalTransfersDisabled": "unverified",
            "universalTransfersDisabled": "unverified",
            "ipRestricted": _ip_restricted(record.get("ip")),
            "liveEligible": False,
        }

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

    return _unverified_permissions("no supported authenticated key-scope endpoint")
