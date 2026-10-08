from __future__ import annotations

import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any

from .contracts import (
    ExecutionRequirements,
    NormalizedDepth,
    OrderRequest,
    VenueCapabilities,
    VenueInfo,
)
from .depth import normalize_depth
from arbx.execution_gate import ExecutionAuthorizationError


class AdapterError(RuntimeError):
    pass


def safe_ccxt_error(exc: Exception, exchange=None, credentials: dict | None = None) -> str:
    """Format transport errors without returning credential values or signatures."""
    message = str(exc)[:1000]
    values = list((credentials or {}).values())
    if exchange is not None:
        values.extend(
            getattr(exchange, name, None)
            for name in ("apiKey", "secret", "password", "privateKey")
        )
    for value in values:
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        if isinstance(value, str) and value:
            message = message.replace(value, "[REDACTED]")
    message = re.sub(
        r"(?i)(api[-_]?key|signature|sign|passphrase|secret|private[-_]?key)=([^&\s]+)",
        r"\1=[REDACTED]",
        message,
    )
    message = re.sub(
        r"-----BEGIN [^-]+-----.*?-----END [^-]+-----",
        "[REDACTED PRIVATE KEY]",
        message,
        flags=re.S,
    )
    return (
        f"{type(exc).__name__}: {message}"[:1200]
        if message
        else f"{type(exc).__name__}: [redacted]"
    )


def ccxt_transport_config(venue_id: str, auth_mode: str, credentials: dict) -> dict:
    """Return CCXT constructor credentials, keeping key profiles intact until transport setup."""
    params = {key: value for key, value in credentials.items() if value}
    if "privateKey" in params and (
        venue_id != "binance" or auth_mode not in ("rsa", "ed25519")
    ):
        raise ValueError("Private-key authentication profile is unsupported for this venue or mode")
    if "privateKey" in params:
        # Binance's CCXT signer inspects PEM text before its EdDSA helper
        # encodes the key. Passing bytes here makes that inspection fail.
        params["secret"] = params.pop("privateKey")
    return params


class BaseAdapter(ABC):
    adapter_name = "base"

    def __init__(self, venue_id: str):
        self.venue_id = venue_id

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    async def get_venue_info(self) -> VenueInfo:
        return VenueInfo(self.venue_id, self.venue_id, self.adapter_name, self.adapter_name)

    async def get_capabilities(self) -> VenueCapabilities:
        return VenueCapabilities()

    async def get_markets(self) -> list[dict[str, Any]]:
        raise AdapterError("market discovery is not implemented")

    async def get_market(self, symbol: str) -> dict[str, Any]:
        raise AdapterError("market lookup is not implemented")

    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        raise AdapterError("ticker is not implemented")

    async def get_order_book(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        raise AdapterError("order book is not implemented")

    async def get_balances(self) -> dict[str, Any]:
        raise AdapterError("balances are not implemented")

    async def create_order(self, request: OrderRequest) -> dict[str, Any]:
        raise AdapterError("order creation is not implemented")

    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        raise AdapterError("order cancellation is not implemented")

    async def get_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        raise AdapterError("order lookup is not implemented")

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        raise AdapterError("open orders are not implemented")

    async def get_fees(self, symbol: str) -> dict[str, Any]:
        raise AdapterError("fees are not implemented")

    async def health_check(self) -> dict[str, Any]:
        return {"ok": False, "reason": "health_check_not_implemented"}

    async def subscribe_order_book(self, symbol: str, limit: int = 20):
        raise AdapterError("order-book subscription is not implemented")

    async def verify_rest(self) -> dict[str, Any]:
        raise AdapterError("REST verification is not implemented")

    async def verify_public_stream(self, symbol: str, limit: int) -> dict[str, Any]:
        raise AdapterError("public stream verification is not implemented")

    async def verify_private_stream(self) -> dict[str, Any]:
        raise AdapterError("private stream verification is not implemented")

    async def verify_permissions(self) -> dict[str, Any]:
        raise AdapterError("permission verification is not implemented")

    async def verify_execution(self, symbol: str) -> dict[str, Any]:
        raise AdapterError("execution verification is not implemented")

    async def watch_depth(self, symbol: str, limit: int) -> NormalizedDepth:
        raise AdapterError("depth streaming is not implemented")


class CCXTProAdapter(BaseAdapter):
    """CCXT Pro is a transport adapter only; the Nu-Arb contract remains authoritative."""

    adapter_name = "ccxt_pro"

    def __init__(self, venue_id, ccxt_id=None, credentials=None, execution_gate=None, *, auth_mode="hmac"):
        super().__init__(venue_id)
        self.ccxt_id = ccxt_id or venue_id
        self.credentials = credentials or {}
        self.ex = None
        self.execution_gate = execution_gate
        self.auth_mode = auth_mode

    async def connect(self):
        import ccxt.pro as ccxtpro

        cls = getattr(ccxtpro, self.ccxt_id, None)
        if cls is None:
            raise AdapterError(f"CCXT Pro adapter unavailable: {self.ccxt_id}")
        params = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        params.update(ccxt_transport_config(self.venue_id, self.auth_mode, self.credentials))
        self.ex = cls(params)
        await self.ex.load_markets()

    async def close(self):
        if self.ex is not None:
            await self.ex.close()

    async def get_venue_info(self):
        return VenueInfo(self.venue_id, self.venue_id, self.adapter_name, "ccxt_pro")

    async def get_capabilities(self):
        has = self.ex.has if self.ex is not None else {}
        return VenueCapabilities(
            spot=True,
            rest=True,
            websocket=has.get("watchOrderBook") is True,
            native_rest=False,
            native_websocket=False,
            ticker=has.get("fetchTicker") is True,
            trades=has.get("watchTrades") is True,
            order_book=has.get("fetchOrderBook") is True,
            order_book_depth=has.get("watchOrderBook") is True,
            user_stream=has.get("watchBalance") is True,
            order_stream=has.get("watchOrders") is True,
            fill_stream=has.get("watchMyTrades") is True,
            balance_stream=has.get("watchBalance") is True,
            market_orders=has.get("createMarketOrder") is True,
            limit_orders=has.get("createOrder") is True,
            ioc=self._ioc_supported(),
            cancel_orders=has.get("cancelOrder") is True,
            balances=has.get("fetchBalance") is True,
            maker_fees=has.get("fetchTradingFee") is True,
            taker_fees=has.get("fetchTradingFee") is True,
            ccxt_pro=True,
        )

    def _ioc_supported(self) -> bool:
        feature_value = getattr(self.ex, "feature_value", None)
        if not callable(feature_value):
            return False
        try:
            tif = feature_value("BTC/USDT", "createOrder", "timeInForce")
            return isinstance(tif, dict) and tif.get("IOC") is True
        except Exception:
            return False

    async def get_markets(self):
        return list((self.ex.markets or {}).values())

    async def get_market(self, symbol):
        return self.ex.markets.get(symbol) or {}

    async def get_ticker(self, symbol):
        return await self.ex.fetch_ticker(symbol)

    async def get_order_book(self, symbol, limit=20):
        return await self.ex.fetch_order_book(symbol, limit)

    async def get_balances(self):
        return await self.ex.fetch_balance()

    async def create_order(self, request: OrderRequest, permit=None, *,
                           route_id: str = "", opportunity_id: str = "",
                           order_scope=None):
        if self.execution_gate is None:
            raise AdapterError("order submission blocked: no central execution gate is attached")
        if permit is None or order_scope is None:
            raise AdapterError("order submission blocked: typed central permit and order evidence are required")
        try:
            self.execution_gate.authorize_order(
                permit, order_scope, strategy="hybrid", route_id=route_id,
                opportunity_id=opportunity_id,
            )
        except ExecutionAuthorizationError as exc:
            raise AdapterError(f"order submission blocked by central execution gate: {exc}") from exc
        self.execution_gate.note_order_attempt(permit, order_scope)
        params = {}
        if request.time_in_force:
            params["timeInForce"] = request.time_in_force
        if request.post_only:
            params["postOnly"] = True
        if request.reduce_only:
            params["reduceOnly"] = True
        if request.client_order_id:
            params["clientOrderId"] = request.client_order_id
        return await self.ex.create_order(
            request.symbol, request.order_type, request.side, request.quantity,
            request.price, params
        )

    async def cancel_order(self, order_id, symbol):
        return await self.ex.cancel_order(order_id, symbol)

    async def get_order(self, order_id, symbol):
        return await self.ex.fetch_order(order_id, symbol)

    async def get_open_orders(self, symbol=None):
        return await self.ex.fetch_open_orders(symbol)

    async def get_fees(self, symbol):
        if self.ex.has.get("fetchTradingFee") is True:
            return await self.ex.fetch_trading_fee(symbol)
        market = self.ex.markets.get(symbol) or {}
        return {"maker": market.get("maker"), "taker": market.get("taker")}

    async def health_check(self):
        try:
            result = await self.verify_rest()
            return {"ok": bool(result.get("ok")), "adapter": self.adapter_name, "venue": self.venue_id, **result}
        except Exception as exc:
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "venue": self.venue_id,
                "error": safe_ccxt_error(exc, self.ex, self.credentials),
            }

    async def verify_rest(self):
        started = time.perf_counter()
        if self.ex.has.get("fetchTime") is True:
            server_ms = await self.ex.fetch_time()
        else:
            await self.ex.fetch_markets()
            server_ms = None
        return {
            "ok": True,
            "rttMs": round((time.perf_counter() - started) * 1000, 1),
            "serverTimeMs": server_ms,
            "marketsLoaded": bool(self.ex.markets),
        }

    async def verify_public_stream(self, symbol, limit=50):
        started = time.perf_counter()
        if self.ex.has.get("watchOrderBook") is not True:
            return {"ok": False, "reason": "watchOrderBook unavailable"}
        book = await self.ex.watch_order_book(symbol, limit)
        ok = bool(book.get("bids") and book.get("asks"))
        return {
            "ok": ok,
            "rttMs": round((time.perf_counter() - started) * 1000, 1),
            "sequence": book.get("nonce"),
            "exchangeTimestampMs": book.get("timestamp"),
            "book": book,
        }

    async def subscribe_order_book(self, symbol, limit=20):
        while True:
            yield await self.ex.watch_order_book(symbol, limit)

    async def watch_depth(self, symbol, limit=50):
        result = await self.verify_public_stream(symbol, limit)
        if not result.get("ok"):
            raise AdapterError(result.get("reason", "public order book unavailable"))
        return normalize_depth(self.venue_id, symbol, result["book"], limit=limit)

    async def verify_private_stream(self):
        if self.ex.has.get("watchBalance") is not True:
            return {"ok": False, "reason": "watchBalance unavailable"}
        balance = await self.ex.watch_balance()
        ok = isinstance(balance, dict) and all(k in balance for k in ("free", "used", "total"))
        return {"ok": ok, "balanceKeys": sorted(k for k in ("free", "used", "total") if isinstance(balance, dict) and k in balance)}

    async def verify_permissions(self):
        from arbx.permissions import inspect_permissions
        return await inspect_permissions(self.venue_id, self.ex)

    async def verify_execution(self, symbol):
        market = self.ex.markets.get(symbol) or {}
        feature_value = getattr(self.ex, "feature_value", None)
        tif = None
        if callable(feature_value):
            try:
                tif = feature_value(symbol, "createOrder", "timeInForce")
            except Exception:
                pass
        return {
            "market": bool(market),
            "spot": bool(market.get("spot")),
            "contract": bool(market.get("contract")),
            "createOrder": self.ex.has.get("createOrder") is True,
            "fetchOrder": self.ex.has.get("fetchOrder") is True,
            "cancelOrder": self.ex.has.get("cancelOrder") is True,
            "watchOrderBook": self.ex.has.get("watchOrderBook") is True,
            "createMarketOrder": self.ex.has.get("createMarketOrder") is True,
            "iocLimit": isinstance(tif, dict) and tif.get("IOC") is True,
        }


class CCXTAdapter(CCXTProAdapter):
    """REST-capable fallback. It deliberately does not claim private/public WS support."""

    adapter_name = "ccxt"

    async def connect(self):
        import ccxt.async_support as ccxt

        cls = getattr(ccxt, self.ccxt_id, None)
        if cls is None:
            raise AdapterError(f"CCXT adapter unavailable: {self.ccxt_id}")
        params = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        params.update(ccxt_transport_config(self.venue_id, self.auth_mode, self.credentials))
        self.ex = cls(params)
        await self.ex.load_markets()

    async def get_capabilities(self):
        base = await super().get_capabilities()
        if hasattr(base, "__dict__"):
            values = dict(base.__dict__)
        elif hasattr(base, "__dataclass_fields__"):
            values = {
                name: getattr(base, name)
                for name in base.__dataclass_fields__
            }
        else:
            values = {}

        values.update(
            {
                "websocket": False,
                "native_websocket": False,
                "user_stream": False,
                "balance_stream": False,
                "ccxt": True,
                "ccxt_pro": False,
            }
        )

        return VenueCapabilities(**values)


class NativeSDKAdapter(BaseAdapter):
    """Fail-closed native-SDK slot.

    A concrete venue implementation must be registered before it can become
    execution eligible. This prevents a claimed native integration from being
    confused with an installed SDK or a catalog entry.
    """

    adapter_name = "native_sdk"
    sdk_module = ""

    def __init__(self, venue_id, credentials=None):
        super().__init__(venue_id)
        self.credentials = credentials or {}
        self.client = None

    async def connect(self):
        if not self.sdk_module:
            raise AdapterError(f"native SDK adapter not implemented for {self.venue_id}")
        try:
            __import__(self.sdk_module)
        except ImportError as exc:
            raise AdapterError(f"native SDK dependency unavailable: {self.sdk_module}") from exc
        raise AdapterError(f"native SDK transport not implemented for {self.venue_id}")

    async def close(self):
        if self.client is not None and hasattr(self.client, "close"):
            result = self.client.close()
            if hasattr(result, "__await__"):
                await result

    async def get_capabilities(self):
        return VenueCapabilities()


class ExternalProcessAdapter(BaseAdapter):
    """Optional strategy bridge; it can never grant venue live eligibility."""

    def __init__(self, venue_id, command_env):
        super().__init__(venue_id)
        self.command_env = command_env
        self.command = os.getenv(command_env, "")

    async def connect(self):
        if not self.command:
            raise AdapterError(f"{self.command_env} is not configured")

    async def close(self):
        pass

    async def verify_rest(self):
        return {"ok": False, "reason": "external strategy bridge"}

    async def verify_public_stream(self, symbol, limit):
        return {"ok": False, "reason": "external strategy bridge"}

    async def verify_private_stream(self):
        return {"ok": False, "reason": "external strategy bridge"}

    async def verify_permissions(self):
        return {"liveEligible": False, "reason": "external bridge requires canonical venue evidence"}

    async def verify_execution(self, symbol):
        return {"executionEligible": False, "reason": "external bridge is strategy-only"}


def optional_strategy_adapters():
    return {
        "freqtrade": lambda venue: ExternalProcessAdapter(venue, "NU_ARB_FREQTRADE_CMD"),
        "hummingbot": lambda venue: ExternalProcessAdapter(venue, "NU_ARB_HUMMINGBOT_CMD"),
    }


def native_adapter_factories():
    # Native integrations are intentionally explicit. An entry is only live-capable
    # after its concrete SDK implementation is added and independently certified.
    return {}
