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
        "validationStatus": "unverified",
        "validationComplete": False,
        "policyBlockers": ["permission_evidence_unavailable"],
        "tradePermission": "unverified",
        "spotAndMarginTradePermission": "unverified",
        "withdrawalsDisabled": "unverified",
        "internalTransfersDisabled": "unverified",
        "universalTransfersDisabled": "unverified",
        "ipRestricted": "unverified",
        "liveEligible": False,
    }


def _validation_fields(complete: bool, blockers: list[str]) -> dict:
    return {
        "validationStatus": "verified" if complete else "unverified",
        "validationComplete": complete,
        "policyBlockers": blockers,
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
    if any(network.prefixlen == 0 for network in networks):
        return False
    for version in (4, 6):
        same_family = [network for network in networks if network.version == version]
        if any(
            network.prefixlen == 0
            for network in ipaddress.collapse_addresses(same_family)
        ):
            return False
    return True


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
        blockers = []
        if not complete:
            blockers.append("permission_fields_incomplete")
        if not trade_ok:
            blockers.append("spot_and_margin_trading_not_enabled")
        if not withdrawals_off:
            blockers.append("withdrawals_not_proven_disabled")
        if not internal_off:
            blockers.append("internal_transfers_not_proven_disabled")
        if not universal_off:
            blockers.append("universal_transfers_not_proven_disabled")
        if not ip_restricted:
            blockers.append("ip_allowlist_not_proven")

        return {
            "source": "Binance GET /sapi/v1/account/apiRestrictions",
            "sourceUrl": "https://developers.binance.com/en/docs/products/wallet/capital/account/API-key-permission",
            **_validation_fields(complete, blockers),
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
        ret_code = raw.get("retCode") if isinstance(raw, dict) else None
        if type(ret_code) is not int or ret_code != 0:
            result = _unverified_permissions(
                "Bybit GET /v5/user/query-api returned a non-success response"
            )
            if type(ret_code) is int:
                result["permissionErrorCode"] = ret_code
                if ret_code == 10010:
                    result["source"] = (
                        "Bybit GET /v5/user/query-api returned retCode 10010 "
                        "(request IP is not allowlisted)"
                    )
                    result["policyBlockers"] = ["ip_allowlist_not_proven"]
                    result["authenticationTransportReached"] = True
            return result
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
        read_only_valid = (
            type(read_only) is bool or (type(read_only) is int and read_only in (0, 1))
        )
        read_only_disabled = (
            (type(read_only) is bool and read_only is False)
            or (type(read_only) is int and read_only == 0)
        )
        trade_enabled = read_only_disabled and "SpotTrade" in spot_scopes
        withdrawals_disabled = not bool(wallet_scopes & {"Withdrawal", "Withdraw"})
        transfers_disabled = not bool(wallet_scopes & {
            "AccountTransfer", "SubMemberTransfer", "SubaccountTransfer", "UniversalTransfer"
        })
        complete = read_only_valid and isinstance(result.get("ips"), list)
        blockers = []
        if not complete:
            blockers.append("permission_fields_incomplete")
        if not trade_enabled:
            blockers.append("spot_trade_not_enabled_or_key_is_read_only")
        if active_non_spot:
            blockers.append("non_spot_permissions_present")
        if not withdrawals_disabled:
            blockers.append("withdrawals_not_proven_disabled")
        if not transfers_disabled:
            blockers.append("transfers_not_proven_disabled")
        if ip_restricted is not True:
            blockers.append("ip_allowlist_not_proven")
        return {
            "source": "Bybit GET /v5/user/query-api",
            "sourceUrl": "https://bybit-exchange.github.io/docs/v5/user/query-api",
            **_validation_fields(complete, blockers),
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
        if not isinstance(raw, dict) or raw.get("code") != "200000":
            return _unverified_permissions("KuCoin GET /api/v1/user/api-key did not return success code 200000")
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
        blockers = []
        if not spot_trade:
            blockers.append("spot_trade_not_enabled")
        if not scope_safe:
            blockers.append("non_spot_or_unrecognized_permissions_present")
        if not withdrawals_disabled:
            blockers.append("withdrawal_permission_present")
        if not transfers_disabled:
            blockers.append("transfer_permission_present")
        if ip_restricted is not True:
            blockers.append("ip_allowlist_not_proven")
        return {
            "source": "KuCoin GET /api/v1/user/api-key",
            "sourceUrl": "https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-api-key-info",
            **_validation_fields(True, blockers),
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
        if not isinstance(raw, dict) or raw.get("status") != "ok":
            return _unverified_permissions("HTX API-key info endpoint did not return status ok")
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
        if isinstance(raw_scopes, str):
            scopes = {item.strip().lower() for item in raw_scopes.split(",") if item.strip()}
        elif isinstance(raw_scopes, list) and all(isinstance(item, str) for item in raw_scopes):
            scopes = {item.strip().lower() for item in raw_scopes if item.strip()}
        else:
            scopes = set()
        if not scopes:
            return _unverified_permissions("HTX API-key info response omitted permission scopes")
        ip_restricted = _ip_restricted(
            record.get("ipAddresses", record.get("ipWhitelist", record.get("ip")))
        )
        trade_enabled = "trade" in scopes
        withdrawals_disabled = not bool(scopes & {"withdraw", "withdrawal"})
        blockers = []
        if not trade_enabled:
            blockers.append("trade_permission_not_enabled")
        if not withdrawals_disabled:
            blockers.append("withdrawal_permission_present")
        if ip_restricted is not True:
            blockers.append("ip_allowlist_not_proven")
        blockers.extend((
            "spot_only_trade_scope_not_proven",
            "internal_transfer_restrictions_not_proven",
            "universal_transfer_restrictions_not_proven",
        ))
        return {
            "source": "HTX API-key info endpoint",
            **_validation_fields(True, blockers),
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
        complete = isinstance(can_trade, bool) and isinstance(can_withdraw, bool)
        blockers = []
        if not complete:
            blockers.append("account_permission_flags_incomplete")
        if can_trade is not True:
            blockers.append("spot_trading_not_proven_enabled")
        if permissions != ["SPOT"]:
            blockers.append("spot_only_scope_not_proven")
        if can_withdraw is not False:
            blockers.append("withdrawals_not_proven_disabled")
        blockers.extend((
            "internal_transfer_restrictions_not_proven",
            "universal_transfer_restrictions_not_proven",
            "ip_allowlist_not_proven",
        ))
        return {
            "source": "MEXC GET /api/v3/account",
            **_validation_fields(complete, blockers),
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
        if (not isinstance(raw, dict) or raw.get("code") != "0"
                or record is None or not isinstance(record.get("perm"), str)
                or "ip" not in record):
            return _unverified_permissions("OKX account config omitted API-key permission scopes")
        scopes = {item.strip().lower() for item in record["perm"].split(",") if item.strip()}
        if not scopes:
            return _unverified_permissions("OKX account config returned empty API-key permission scopes")
        trade_enabled = "trade" in scopes
        withdrawals_disabled = "withdraw" not in scopes
        ip_restricted = _ip_restricted(record.get("ip"))
        blockers = []
        if not trade_enabled:
            blockers.append("trade_permission_not_enabled")
        if not withdrawals_disabled:
            blockers.append("withdrawal_permission_present")
        blockers.extend((
            "spot_only_trade_scope_not_proven",
            "internal_transfer_restrictions_not_proven",
            "universal_transfer_restrictions_not_proven",
        ))
        if ip_restricted is not True:
            blockers.append("ip_allowlist_not_proven")
        return {
            "source": "OKX GET /api/v5/account/config",
            **_validation_fields(True, blockers),
            "accountPermissionScopes": sorted(scopes),
            "tradePermission": "enabled" if trade_enabled else "disabled",
            "spotAndMarginTradePermission": "unverified",
            "withdrawalsDisabled": withdrawals_disabled,
            "internalTransfersDisabled": "unverified",
            "universalTransfersDisabled": "unverified",
            "ipRestricted": ip_restricted,
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
            return _unverified_permissions("Bitfinex permission endpoint returned an invalid response")

        scopes = {}
        for item in raw:
            if (not isinstance(item, (list, tuple)) or len(item) != 3
                    or not isinstance(item[0], str)
                    or type(item[1]) is not int or item[1] not in (0, 1)
                    or type(item[2]) is not int or item[2] not in (0, 1)
                    or item[0] in scopes):
                return _unverified_permissions("Bitfinex permission endpoint returned an invalid scope")
            scopes[item[0]] = {"read": bool(item[1]), "write": bool(item[2])}

        required_scopes = {"orders", "wallets", "withdraw", "funding", "positions"}
        complete = required_scopes <= scopes.keys()
        order_write = scopes.get("orders", {}).get("write", "unverified")
        withdraw_write = scopes.get("withdraw", {}).get("write", "unverified")
        blockers = []
        if not complete:
            blockers.append("permission_fields_incomplete")
        if order_write is not True:
            blockers.append("trade_permission_not_enabled")
        if withdraw_write is not False:
            blockers.append("withdrawal_permission_not_proven_disabled")
        blockers.extend((
            "spot_only_trade_scope_not_proven",
            "internal_transfer_restrictions_not_proven",
            "universal_transfer_restrictions_not_proven",
            "ip_allowlist_not_proven",
        ))
        return {
            "source": "Bitfinex POST /v2/auth/r/permissions",
            "sourceUrl": "https://docs.bitfinex.com/reference/key-permissions",
            **_validation_fields(complete, blockers),
            "accountPermissionScopes": scopes,
            "tradePermission": "enabled" if order_write is True else "disabled" if order_write is False else "unverified",
            "spotAndMarginTradePermission": "unverified",
            "withdrawalsDisabled": withdraw_write is False,
            "internalTransfersDisabled": "unverified",
            "universalTransfersDisabled": "unverified",
            "ipRestricted": "unverified",
            "liveEligible": False,
            "orderWritePermission": order_write,
            "withdrawWritePermission": withdraw_write,
            "fundingWritePermission": scopes.get("funding", {}).get("write", "unverified"),
            "positionsWritePermission": scopes.get("positions", {}).get("write", "unverified"),
        }

    return _unverified_permissions("no supported authenticated key-scope endpoint")
