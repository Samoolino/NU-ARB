from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol

@dataclass(frozen=True, slots=True)
class VenueCapability:
    venue_id: str
    adapter: str
    market_types: tuple[str, ...] = ("spot",)
    public_ws: bool = False
    private_ws: bool = False
    orderbook: bool = False
    create_order: bool = False
    fetch_order: bool = False
    cancel_order: bool = False
    ioc: bool = False
    authenticated_balance: bool = False
    permission_probe: bool = False

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
    def best_bid(self): return self.bids[0].price if self.bids else 0.0
    @property
    def best_ask(self): return self.asks[0].price if self.asks else 0.0

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

class VenueAdapter(Protocol):
    venue_id: str
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def verify_rest(self) -> dict[str, Any]: ...
    async def verify_public_stream(self, symbol: str, limit: int) -> dict[str, Any]: ...
    async def verify_private_stream(self) -> dict[str, Any]: ...
    async def verify_permissions(self) -> dict[str, Any]: ...
    async def verify_execution(self, symbol: str) -> dict[str, Any]: ...
    async def watch_depth(self, symbol: str, limit: int) -> NormalizedDepth: ...
