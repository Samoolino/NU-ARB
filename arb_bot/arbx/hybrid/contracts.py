from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


@dataclass(frozen=True, slots=True)
class VenueInfo:
    venue_id: str
    display_name: str
    adapter: str
    transport: str
    region: str | None = None


@dataclass(frozen=True, slots=True)
class VenueCapabilities:
    spot: bool = False
    margin: bool = False
    futures: bool = False
    perpetuals: bool = False
    options: bool = False
    rest: bool = False
    websocket: bool = False
    native_rest: bool = False
    native_websocket: bool = False
    ticker: bool = False
    trades: bool = False
    order_book: bool = False
    order_book_depth: bool = False
    user_stream: bool = False
    order_stream: bool = False
    fill_stream: bool = False
    balance_stream: bool = False
    market_orders: bool = False
    limit_orders: bool = False
    ioc: bool = False
    fok: bool = False
    post_only: bool = False
    reduce_only: bool = False
    cancel_orders: bool = False
    amend_orders: bool = False
    batch_orders: bool = False
    balances: bool = False
    positions: bool = False
    maker_fees: bool = False
    taker_fees: bool = False
    funding_rates: bool = False
    deposits: bool = False
    withdrawals: bool = False
    internal_transfer: bool = False
    native_sdk: bool = False
    ccxt: bool = False
    ccxt_pro: bool = False
    hummingbot: bool = False
    nautilus: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionRequirements:
    market_type: str = "spot"
    order_type: str = "limit"
    min_liquidity_usd: float = 0.0
    require_websocket: bool = True
    require_private_stream: bool = True
    require_ioc: bool = False
    require_post_only: bool = False
    max_latency_ms: float | None = None
    max_slippage_bps: float | None = None
    require_market_order: bool = False


@dataclass(frozen=True, slots=True)
class DepthLevel:
    price: float
    amount: float


@dataclass(frozen=True, slots=True)
class NormalizedDepth:
    venue: str
    symbol: str
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    received_monotonic: float
    exchange_timestamp_ms: int | None = None
    sequence: int | str | None = None

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0


@dataclass(frozen=True, slots=True)
class DepthValidation:
    ok: bool
    reason: str
    depth_usd: float = 0.0
    top_bid: float = 0.0
    top_ask: float = 0.0
    spread_bps: float = 0.0
    levels: int = 0


@dataclass(frozen=True, slots=True)
class VenueEvidence:
    venue: str
    adapter: str
    rest_ok: bool
    public_ws_ok: bool
    private_ws_ok: bool
    balance_ok: bool
    permission_ok: bool
    execution_ok: bool
    depth_ok: bool
    live_eligible: bool
    reasons: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float | None = None
    time_in_force: str | None = None
    post_only: bool = False
    reduce_only: bool = False
    client_order_id: str | None = None


class VenueAdapter(Protocol):
    venue_id: str
    adapter_name: str

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def get_venue_info(self) -> VenueInfo: ...
    async def get_capabilities(self) -> VenueCapabilities: ...
    async def get_markets(self) -> list[dict[str, Any]]: ...
    async def get_market(self, symbol: str) -> dict[str, Any]: ...
    async def get_ticker(self, symbol: str) -> dict[str, Any]: ...
    async def get_order_book(self, symbol: str, limit: int = 20) -> dict[str, Any]: ...
    async def subscribe_order_book(self, symbol: str, limit: int = 20) -> AsyncIterator[dict[str, Any]]: ...
    async def get_balances(self) -> dict[str, Any]: ...
    async def create_order(self, request: OrderRequest) -> dict[str, Any]: ...
    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]: ...
    async def get_order(self, order_id: str, symbol: str) -> dict[str, Any]: ...
    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    async def get_fees(self, symbol: str) -> dict[str, Any]: ...
    async def health_check(self) -> dict[str, Any]: ...
    async def verify_rest(self) -> dict[str, Any]: ...
    async def verify_public_stream(self, symbol: str, limit: int) -> dict[str, Any]: ...
    async def verify_private_stream(self) -> dict[str, Any]: ...
    async def verify_permissions(self) -> dict[str, Any]: ...
    async def verify_execution(self, symbol: str) -> dict[str, Any]: ...
    async def watch_depth(self, symbol: str, limit: int) -> NormalizedDepth: ...
