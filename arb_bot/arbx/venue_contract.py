"""Canonical, typed venue-adapter contract.

This module defines interfaces and evidence models only. It is deliberately
not wired into the legacy or hybrid adapter, scanner, or execution paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import AsyncIterator, Mapping, Protocol, runtime_checkable


class Capability(str, Enum):
    REST = "rest"
    PUBLIC_STREAM = "public_stream"
    PRIVATE_STREAM = "private_stream"
    MARKET_DISCOVERY = "market_discovery"
    MARKET_LOOKUP = "market_lookup"
    TICKER = "ticker"
    ORDER_BOOK = "order_book"
    ORDER_BOOK_STREAM = "order_book_stream"
    BALANCES = "balances"
    POSITIONS = "positions"
    OPEN_ORDERS = "open_orders"
    ORDER_LOOKUP = "order_lookup"
    ORDER_CREATE = "order_create"
    ORDER_CANCEL = "order_cancel"
    FEES = "fees"
    FUNDING = "funding"
    DEPOSIT_ADDRESS = "deposit_address"
    WITHDRAWAL = "withdrawal"
    INTERNAL_TRANSFER = "internal_transfer"
    HEALTH = "health"
    LATENCY = "latency"
    AUTHENTICATION_CHECK = "authentication_check"
    PERMISSION_CHECK = "permission_check"
    EXECUTION_CERTIFICATION = "execution_certification"


class CapabilityStatus(str, Enum):
    """Adapter declaration, independent from runtime evidence."""

    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"
    IMPLEMENTED = "implemented"


class EvidenceStatus(str, Enum):
    """Outcome of an evidence check; VERIFIED is not a trading authorization."""

    NOT_CHECKED = "not_checked"
    VERIFIED = "verified"
    FAILED = "failed"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class CapabilityDeclaration:
    status: CapabilityStatus = CapabilityStatus.UNVERIFIED
    source: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    status: EvidenceStatus = EvidenceStatus.NOT_CHECKED
    checked_at: datetime | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    """Capability declarations and observations are stored separately."""

    declarations: Mapping[Capability, CapabilityDeclaration] = field(default_factory=dict)
    evidence: Mapping[Capability, EvidenceRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "declarations", MappingProxyType(dict(self.declarations)))
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))

    def declaration_for(self, capability: Capability) -> CapabilityDeclaration:
        return self.declarations.get(capability, CapabilityDeclaration())

    def evidence_for(self, capability: Capability) -> EvidenceRecord:
        return self.evidence.get(capability, EvidenceRecord())


class MarketType(str, Enum):
    SPOT = "spot"
    MARGIN = "margin"
    FUTURE = "future"
    PERPETUAL = "perpetual"
    OPTION = "option"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderKind(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderState(str, Enum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class VenueIdentity:
    venue_id: str
    display_name: str
    adapter_id: str
    transport: str
    region: str | None = None


@dataclass(frozen=True, slots=True)
class MarketInfo:
    symbol: str
    base: str
    quote: str
    market_type: MarketType
    active: bool | None = None
    price_precision: int | None = None
    quantity_precision: int | None = None


@dataclass(frozen=True, slots=True)
class Ticker:
    symbol: str
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    base_volume: Decimal | None = None
    quote_volume: Decimal | None = None
    timestamp_ms: int | None = None


@dataclass(frozen=True, slots=True)
class DepthLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderBook:
    symbol: str
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    timestamp_ms: int | None = None
    sequence: int | str | None = None


@dataclass(frozen=True, slots=True)
class MarketDepth:
    """A depth snapshot with local receipt time kept distinct from venue time."""

    book: OrderBook
    received_at: datetime


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """REST/bootstrap image for a single venue market and sequence position."""

    venue_id: str
    symbol: str
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    sequence: int
    source_timestamp_ms: int


@dataclass(frozen=True, slots=True)
class OrderBookDelta:
    """Sequence-bounded changes; zero quantity removes a price level."""

    venue_id: str
    symbol: str
    first_sequence: int
    last_sequence: int
    bids: tuple[DepthLevel, ...] = ()
    asks: tuple[DepthLevel, ...] = ()
    previous_sequence: int | None = None
    source_timestamp_ms: int = 0


@dataclass(frozen=True, slots=True)
class TradePrint:
    """Normalized public trade; side is the aggressor side."""

    venue_id: str
    symbol: str
    side: Side
    price: Decimal
    quantity: Decimal
    source_timestamp_ms: int


class BookFeedState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    BACKOFF = "backoff"


class BookApplyStatus(str, Enum):
    APPLIED = "applied"
    REJECTED = "rejected"
    RESYNC_REQUIRED = "resync_required"


@dataclass(frozen=True, slots=True)
class Balance:
    asset: str
    free: Decimal | None = None
    locked: Decimal | None = None
    total: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: Decimal
    side: Side
    entry_price: Decimal | None = None
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    leverage: Decimal | None = None


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    side: Side
    kind: OrderKind
    quantity: Decimal
    price: Decimal | None = None
    time_in_force: str | None = None
    client_order_id: str | None = None
    post_only: bool = False
    reduce_only: bool = False


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    symbol: str
    side: Side
    kind: OrderKind
    state: OrderState
    quantity: Decimal
    filled: Decimal = Decimal(0)
    price: Decimal | None = None
    average_price: Decimal | None = None
    client_order_id: str | None = None
    timestamp_ms: int | None = None


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    maker: Decimal | None = None
    taker: Decimal | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class FundingRate:
    symbol: str
    rate: Decimal
    next_funding_time_ms: int | None = None
    interval_hours: Decimal | None = None


@dataclass(frozen=True, slots=True)
class DepositAddress:
    asset: str
    address: str
    network: str | None = None
    tag_or_memo: str | None = None


@dataclass(frozen=True, slots=True)
class WithdrawalRequest:
    asset: str
    amount: Decimal
    address: str
    network: str | None = None
    tag_or_memo: str | None = None
    client_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class TransferRequest:
    asset: str
    amount: Decimal
    from_account: str
    to_account: str
    client_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class TransferReceipt:
    transfer_id: str
    asset: str
    amount: Decimal
    state: str
    timestamp_ms: int | None = None


class ConnectionChannel(str, Enum):
    REST = "rest"
    PUBLIC_STREAM = "public_stream"
    PRIVATE_STREAM = "private_stream"


@dataclass(frozen=True, slots=True)
class ConnectivityEvidence:
    channel: ConnectionChannel
    status: EvidenceStatus
    checked_at: datetime | None = None
    latency_ms: Decimal | None = None
    detail: str | None = None


class HealthState(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class LatencySample:
    operation: str
    latency_ms: Decimal
    measured_at: datetime


@dataclass(frozen=True, slots=True)
class HealthReport:
    state: HealthState
    checked_at: datetime | None = None
    latency: tuple[LatencySample, ...] = ()
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AuthenticationEvidence:
    evidence: EvidenceRecord
    method: str | None = None


class Permission(str, Enum):
    READ = "read"
    TRADE = "trade"
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"
    TRANSFER = "transfer"


@dataclass(frozen=True, slots=True)
class PermissionAssessment:
    permission: Permission
    evidence: EvidenceRecord


@dataclass(frozen=True, slots=True)
class PermissionsEvidence:
    assessments: tuple[PermissionAssessment, ...] = ()
    checked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExecutionCertification:
    evidence: EvidenceRecord
    symbol: str
    profile: str | None = None
    findings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PrivateEvent:
    event_type: str
    timestamp_ms: int | None = None
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@runtime_checkable
class VenueAdapter(Protocol):
    """Typed adapter surface; declarations do not imply runtime verification."""

    @property
    def identity(self) -> VenueIdentity: ...

    @property
    def capabilities(self) -> CapabilityReport: ...

    async def connect(self) -> None: ...
    async def close(self) -> None: ...

    async def get_markets(self) -> tuple[MarketInfo, ...]: ...
    async def get_market(self, symbol: str) -> MarketInfo | None: ...
    async def get_ticker(self, symbol: str) -> Ticker: ...
    async def get_order_book(self, symbol: str, limit: int = 20) -> OrderBook: ...
    async def get_depth(self, symbol: str, limit: int = 20) -> MarketDepth: ...
    def stream_order_book(self, symbol: str, limit: int = 20) -> AsyncIterator[OrderBook]: ...
    def stream_private_events(self) -> AsyncIterator[PrivateEvent]: ...

    async def get_balances(self) -> tuple[Balance, ...]: ...
    async def get_positions(self) -> tuple[Position, ...]: ...
    async def get_open_orders(self, symbol: str | None = None) -> tuple[Order, ...]: ...
    async def get_order(self, order_id: str, symbol: str) -> Order | None: ...
    async def create_order(self, request: OrderRequest) -> Order: ...
    async def cancel_order(self, order_id: str, symbol: str) -> Order: ...

    async def get_fees(self, symbol: str) -> FeeSchedule: ...
    async def get_funding_rate(self, symbol: str) -> FundingRate: ...
    async def get_deposit_address(self, asset: str, network: str | None = None) -> DepositAddress: ...
    async def withdraw(self, request: WithdrawalRequest) -> TransferReceipt: ...
    async def transfer(self, request: TransferRequest) -> TransferReceipt: ...

    async def verify_rest(self) -> ConnectivityEvidence: ...
    async def verify_public_stream(self, symbol: str) -> ConnectivityEvidence: ...
    async def verify_private_stream(self) -> ConnectivityEvidence: ...
    async def get_health(self) -> HealthReport: ...
    async def verify_authentication(self) -> AuthenticationEvidence: ...
    async def verify_permissions(self) -> PermissionsEvidence: ...
    async def certify_execution(self, symbol: str) -> ExecutionCertification: ...
