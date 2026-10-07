"""Deterministic, isolated paper/sandbox order-transport simulation.

This module accepts only caller-supplied typed market, book, order, and balance
data. It has no adapter, credential, socket, or production-execution interface
and is deliberately not wired into the opportunity, risk, or live-order paths.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping

from .venue_contract import (
    Balance,
    DepthLevel,
    MarketInfo,
    MarketType,
    Order,
    OrderBook,
    OrderKind,
    OrderRequest,
    OrderState,
    Side,
)

_ZERO = Decimal(0)
_ONE = Decimal(1)
_BPS = Decimal(10_000)


class SimulationMode(str, Enum):
    PAPER = "paper"
    SANDBOX = "sandbox"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class FeeModel:
    """Explicit fractional quote fee rates (0.001 means 10 bps)."""

    maker_rate: Decimal
    taker_rate: Decimal

    def __post_init__(self) -> None:
        for name, value in (("maker_rate", self.maker_rate), ("taker_rate", self.taker_rate)):
            number = _as_decimal(value, name)
            if number < _ZERO or number >= _ONE:
                raise ValueError(f"{name} must be in [0, 1)")
            object.__setattr__(self, name, number)

    def rate(self, *, maker: bool) -> Decimal:
        return self.maker_rate if maker else self.taker_rate


@dataclass(frozen=True, slots=True)
class DepthImpactModel:
    """Depth walking plus deterministic impact proportional to consumed depth."""

    impact_bps_per_depth_fraction: Decimal = _ZERO

    def __post_init__(self) -> None:
        value = _as_decimal(self.impact_bps_per_depth_fraction, "impact_bps_per_depth_fraction")
        if value < _ZERO or value >= _BPS:
            raise ValueError("impact_bps_per_depth_fraction must be in [0, 10,000)")
        object.__setattr__(self, "impact_bps_per_depth_fraction", value)

    def adjusted_price(
        self, side: Side, raw_price: Decimal, consumed_quote_fraction: Decimal
    ) -> Decimal:
        impact = self.impact_bps_per_depth_fraction * consumed_quote_fraction / _BPS
        multiplier = _ONE + impact if side is Side.BUY else _ONE - impact
        if multiplier <= _ZERO:
            raise ValueError("configured market impact produces a nonpositive price")
        return raw_price * multiplier


@dataclass(frozen=True, slots=True)
class LatencyModel:
    submit_ms: int = 0
    cancel_ms: int = 0
    fill_ms: int = 0

    def __post_init__(self) -> None:
        values = (self.submit_ms, self.cancel_ms, self.fill_ms)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("latencies must be nonnegative integer milliseconds")


@dataclass(frozen=True, slots=True)
class RateLimitModel:
    max_requests: int
    window_ms: int

    def __post_init__(self) -> None:
        if (isinstance(self.max_requests, bool) or not isinstance(self.max_requests, int)
                or self.max_requests <= 0):
            raise ValueError("max_requests must be a positive integer")
        if isinstance(self.window_ms, bool) or not isinstance(self.window_ms, int) or self.window_ms <= 0:
            raise ValueError("window_ms must be a positive integer")


@dataclass(frozen=True, slots=True)
class RejectionPolicy:
    max_order_quantity: Decimal | None = None
    rejected_symbols: frozenset[str] = frozenset()
    rejected_client_order_ids: frozenset[str] = frozenset()
    reject_next: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_order_quantity is not None:
            value = _as_decimal(self.max_order_quantity, "max_order_quantity")
            if value <= _ZERO:
                raise ValueError("max_order_quantity must be positive")
            object.__setattr__(self, "max_order_quantity", value)
        if any(not isinstance(reason, str) or not reason.strip() for reason in self.reject_next):
            raise ValueError("reject_next reasons must be non-empty strings")
        if any(not isinstance(symbol, str) or not symbol.strip() for symbol in self.rejected_symbols):
            raise ValueError("rejected_symbols must contain non-empty strings")
        if any(not isinstance(client_id, str) or not client_id.strip()
               for client_id in self.rejected_client_order_ids):
            raise ValueError("rejected_client_order_ids must contain non-empty strings")


@dataclass(frozen=True, slots=True)
class CancelFailurePolicy:
    failed_order_ids: frozenset[str] = frozenset()
    fail_next_count: int = 0

    def __post_init__(self) -> None:
        if (isinstance(self.fail_next_count, bool) or not isinstance(self.fail_next_count, int)
                or self.fail_next_count < 0):
            raise ValueError("fail_next_count must be a nonnegative integer")
        if any(not isinstance(order_id, str) or not order_id.strip()
               for order_id in self.failed_order_ids):
            raise ValueError("failed_order_ids must contain non-empty strings")


@dataclass(frozen=True, slots=True)
class NetworkCostModel:
    """Explicit simulated quote-denominated transaction/network cost per order.

    This is an accounting assumption only; it does not model or submit a chain
    transaction, withdrawal, or transfer.
    """

    cost_per_order_quote: Decimal = _ZERO

    def __post_init__(self) -> None:
        value = _as_decimal(self.cost_per_order_quote, "cost_per_order_quote")
        if value < _ZERO:
            raise ValueError("cost_per_order_quote must be nonnegative")
        object.__setattr__(self, "cost_per_order_quote", value)


@dataclass(frozen=True, slots=True)
class BookMovement:
    at_ms: int
    book: OrderBook


@dataclass(frozen=True, slots=True)
class PaperFill:
    fill_id: str
    order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    quote_amount: Decimal
    fee_quote: Decimal
    maker: bool
    timestamp_ms: int


@dataclass(frozen=True, slots=True)
class PaperOrderResult:
    order: Order
    fills: tuple[PaperFill, ...]
    accepted: bool
    reason: str | None
    latency_ms: int
    network_cost_quote: Decimal


@dataclass(frozen=True, slots=True)
class PaperCancelResult:
    order: Order | None
    canceled: bool
    reason: str | None
    latency_ms: int


@dataclass(slots=True)
class _OpenOrder:
    order: Order
    reservation_per_unit: Decimal
    reserved_remaining: Decimal
    reserved_asset: str


@dataclass(frozen=True, slots=True)
class _PlannedFill:
    quantity: Decimal
    price: Decimal
    book_price: Decimal


def _as_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a finite number") from None
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite number")
    return result


def _validate_book(book: OrderBook, symbol: str) -> None:
    if not isinstance(book, OrderBook) or not isinstance(book.symbol, str) or book.symbol != symbol:
        raise ValueError("book must be a typed OrderBook for the configured symbol")
    for name, levels, descending in (
        ("bids", book.bids, True),
        ("asks", book.asks, False),
    ):
        previous: Decimal | None = None
        for level in levels:
            if not isinstance(level, DepthLevel):
                raise ValueError(f"book.{name} must contain typed DepthLevel values")
            if not isinstance(level.price, Decimal) or not isinstance(level.quantity, Decimal):
                raise ValueError(f"book.{name} levels must use Decimal price and quantity")
            price = _as_decimal(level.price, f"book.{name}.price")
            quantity = _as_decimal(level.quantity, f"book.{name}.quantity")
            if price <= _ZERO or quantity <= _ZERO:
                raise ValueError(f"book.{name} levels must have positive price and quantity")
            if previous is not None and ((descending and price >= previous) or
                                         (not descending and price <= previous)):
                raise ValueError(f"book.{name} must be strictly price sorted")
            previous = price
    if not book.bids or not book.asks or book.bids[0].price >= book.asks[0].price:
        raise ValueError("book must have two-sided non-crossed top of book")


class PaperTransport:
    """Stateful deterministic simulator; it is not a VenueAdapter or live transport.

    All market movement, failures, fees, latency, rate limits, and costs are
    caller-configured. There is no probabilistic mode, credential input, adapter
    reference, network I/O, or production order method.
    """

    def __init__(
        self,
        market: MarketInfo,
        book: OrderBook,
        balances: Mapping[str, Decimal],
        *,
        fees: FeeModel,
        impact: DepthImpactModel = DepthImpactModel(),
        latency: LatencyModel = LatencyModel(),
        rate_limit: RateLimitModel | None = None,
        rejections: RejectionPolicy = RejectionPolicy(),
        cancel_failures: CancelFailurePolicy = CancelFailurePolicy(),
        network_costs: NetworkCostModel = NetworkCostModel(),
        book_movements: tuple[BookMovement, ...] = (),
        start_time_ms: int = 0,
        mode: SimulationMode = SimulationMode.PAPER,
        max_fill_quantity_per_call: Decimal | None = None,
    ) -> None:
        if mode is SimulationMode.LIVE:
            raise ValueError("live mode is unsupported; paper/sandbox transport is isolated")
        if mode not in (SimulationMode.PAPER, SimulationMode.SANDBOX):
            raise ValueError("unsupported simulation mode")
        if not isinstance(impact, DepthImpactModel) or not isinstance(latency, LatencyModel):
            raise ValueError("impact and latency must use the explicit simulation models")
        if rate_limit is not None and not isinstance(rate_limit, RateLimitModel):
            raise ValueError("rate_limit must be a RateLimitModel or None")
        if not isinstance(rejections, RejectionPolicy):
            raise ValueError("rejections must be a RejectionPolicy")
        if not isinstance(cancel_failures, CancelFailurePolicy):
            raise ValueError("cancel_failures must be a CancelFailurePolicy")
        if not isinstance(network_costs, NetworkCostModel):
            raise ValueError("network_costs must be a NetworkCostModel")
        if not isinstance(market, MarketInfo) or market.market_type is not MarketType.SPOT:
            raise ValueError("only explicitly typed spot markets are supported")
        if market.active is not True:
            raise ValueError("market must be explicitly active")
        if not market.symbol or not market.base or not market.quote or market.base == market.quote:
            raise ValueError("market symbol, base, and quote are required")
        _validate_book(book, market.symbol)
        if not isinstance(fees, FeeModel):
            raise ValueError("fees must be an explicit FeeModel")
        if isinstance(start_time_ms, bool) or not isinstance(start_time_ms, int) or start_time_ms < 0:
            raise ValueError("start_time_ms must be a nonnegative integer")
        if max_fill_quantity_per_call is not None:
            max_fill_quantity_per_call = _as_decimal(
                max_fill_quantity_per_call, "max_fill_quantity_per_call"
            )
            if max_fill_quantity_per_call <= _ZERO:
                raise ValueError("max_fill_quantity_per_call must be positive")
        if not isinstance(balances, Mapping) or not balances:
            raise ValueError("balances must be a non-empty asset-to-amount mapping")
        self._balances: dict[str, list[Decimal]] = {}
        for asset, amount in balances.items():
            if not isinstance(asset, str) or not asset.strip():
                raise ValueError("balance asset names must be non-empty strings")
            free = _as_decimal(amount, f"balances.{asset}")
            if free < _ZERO:
                raise ValueError("initial balances must be nonnegative")
            normalized_asset = asset.upper()
            if normalized_asset in self._balances:
                raise ValueError("duplicate balance asset after normalization")
            self._balances[normalized_asset] = [free, _ZERO]
        if market.base.upper() not in self._balances or market.quote.upper() not in self._balances:
            raise ValueError("explicit base and quote balances are required")
        previous_at = -1
        if not isinstance(book_movements, tuple):
            raise ValueError("book_movements must be a tuple")
        for movement in book_movements:
            if (not isinstance(movement, BookMovement)
                    or isinstance(movement.at_ms, bool)
                    or not isinstance(movement.at_ms, int)
                    or movement.at_ms < start_time_ms):
                raise ValueError("book movements must be typed and not precede simulation start")
            if movement.at_ms < previous_at:
                raise ValueError("book movements must be sorted by at_ms")
            _validate_book(movement.book, market.symbol)
            previous_at = movement.at_ms

        self.market = market
        self.mode = mode
        self.fees = fees
        self.impact = impact
        self.latency = latency
        self.rate_limit = rate_limit
        self.rejections = rejections
        self.cancel_failures = cancel_failures
        self.network_costs = network_costs
        self.max_fill_quantity_per_call = max_fill_quantity_per_call
        self._book = book
        self._movements = tuple(book_movements)
        self._movement_index = 0
        self._now_ms = start_time_ms
        self._request_times: deque[int] = deque()
        self._reject_index = 0
        self._cancel_failures_left = cancel_failures.fail_next_count
        self._next_order = 1
        self._next_fill = 1
        self._orders: dict[str, _OpenOrder] = {}
        self._fills: list[PaperFill] = []

    @property
    def now_ms(self) -> int:
        return self._now_ms

    @property
    def book(self) -> OrderBook:
        return self._book

    @property
    def fills(self) -> tuple[PaperFill, ...]:
        return tuple(self._fills)

    def balance_snapshot(self) -> tuple[Balance, ...]:
        return tuple(
            Balance(asset, amounts[0], amounts[1], amounts[0] + amounts[1])
            for asset, amounts in sorted(self._balances.items())
        )

    def get_order(self, order_id: str) -> Order | None:
        row = self._orders.get(order_id)
        return row.order if row else None

    def submit(self, request: OrderRequest) -> PaperOrderResult:
        """Simulate one order submission against caller-supplied state."""
        started = self._now_ms
        rate_reason = self._take_request()
        self._advance(self.latency.submit_ms)
        if rate_reason:
            order = self._new_order(request, OrderState.REJECTED)
            return self._result(order, False, rate_reason, started, ())

        reason = self._next_rejection(request)
        if reason is None:
            reason = self._unsupported_request_reason(request)
        if reason is not None:
            order = self._new_order(request, OrderState.REJECTED)
            return self._result(order, False, reason, started, ())

        self._advance(self.latency.fill_ms)
        assert isinstance(request, OrderRequest)
        plan = self._plan_fills(request, request.quantity, maker=False)
        filled_quantity = sum((item.quantity for item in plan), _ZERO)
        if request.time_in_force == "FOK" and filled_quantity != request.quantity:
            order = self._new_order(request, OrderState.REJECTED)
            return self._result(order, False, "fok_not_fully_fillable", started, ())

        reservation_asset, reserve_per_unit = self._reservation(request)
        reservation = reserve_per_unit * request.quantity
        network_cost = self.network_costs.cost_per_order_quote
        quote_free = self._balances[self.market.quote.upper()][0]
        available = self._balances[reservation_asset][0]
        if reservation > available or network_cost > quote_free - (reservation if reservation_asset == self.market.quote.upper() else _ZERO):
            order = self._new_order(request, OrderState.REJECTED)
            return self._result(order, False, "insufficient_balance", started, ())

        self._balances[reservation_asset][0] -= reservation
        self._balances[reservation_asset][1] += reservation
        self._balances[self.market.quote.upper()][0] -= network_cost
        order = self._new_order(request, OrderState.OPEN)
        row = _OpenOrder(order, reserve_per_unit, reservation, reservation_asset)
        self._orders[order.order_id] = row

        fills = self._apply_planned_fills(row, plan, maker=False)
        remaining = request.quantity - row.order.filled
        if remaining == _ZERO:
            self._finish(row, OrderState.FILLED)
        elif request.kind is OrderKind.MARKET or request.time_in_force == "IOC":
            self._finish(row, OrderState.CANCELED)
        elif row.order.filled > _ZERO:
            row.order = self._with_state(row.order, OrderState.PARTIALLY_FILLED)
        return self._result(row.order, True, None, started, fills, network_cost)

    def process_order(self, order_id: str) -> PaperOrderResult:
        """Try another deterministic depth match for an open limit order."""
        started = self._now_ms
        row = self._orders.get(order_id)
        if row is None:
            return self._result(self._unknown_order(order_id), False, "unknown_order", started, ())
        if row.order.state not in (OrderState.OPEN, OrderState.PARTIALLY_FILLED):
            return self._result(row.order, False, "order_not_open", started, ())
        rate_reason = self._take_request()
        self._advance(self.latency.fill_ms)
        if rate_reason:
            return self._result(row.order, False, rate_reason, started, ())
        request = OrderRequest(
            row.order.symbol, row.order.side, row.order.kind, row.order.quantity - row.order.filled,
            row.order.price, "GTC", row.order.client_order_id,
        )
        plan = self._plan_fills(request, request.quantity, maker=True)
        fills = self._apply_planned_fills(row, plan, maker=True)
        if row.order.filled == row.order.quantity:
            self._finish(row, OrderState.FILLED)
        elif row.order.filled > _ZERO:
            row.order = self._with_state(row.order, OrderState.PARTIALLY_FILLED)
        return self._result(row.order, True, None, started, fills)

    def cancel(self, order_id: str) -> PaperCancelResult:
        started = self._now_ms
        rate_reason = self._take_request()
        self._advance(self.latency.cancel_ms)
        row = self._orders.get(order_id)
        if rate_reason:
            return PaperCancelResult(row.order if row else None, False, rate_reason, self._now_ms - started)
        if row is None:
            return PaperCancelResult(None, False, "unknown_order", self._now_ms - started)
        if row.order.state not in (OrderState.OPEN, OrderState.PARTIALLY_FILLED):
            return PaperCancelResult(row.order, False, "order_not_open", self._now_ms - started)
        if (self._cancel_failures_left > 0 or order_id in self.cancel_failures.failed_order_ids):
            if self._cancel_failures_left > 0:
                self._cancel_failures_left -= 1
            return PaperCancelResult(row.order, False, "cancel_failed", self._now_ms - started)
        self._finish(row, OrderState.CANCELED)
        return PaperCancelResult(row.order, True, None, self._now_ms - started)

    def _take_request(self) -> str | None:
        if self.rate_limit is None:
            return None
        cutoff = self._now_ms - self.rate_limit.window_ms
        while self._request_times and self._request_times[0] <= cutoff:
            self._request_times.popleft()
        if len(self._request_times) >= self.rate_limit.max_requests:
            return "rate_limited"
        self._request_times.append(self._now_ms)
        return None

    def _would_cross(self, request: OrderRequest) -> bool:
        assert request.price is not None
        if request.side is Side.BUY:
            return bool(self._book.asks and request.price >= self._book.asks[0].price)
        return bool(self._book.bids and request.price <= self._book.bids[0].price)

    def _advance(self, milliseconds: int) -> None:
        self._now_ms += milliseconds
        while (self._movement_index < len(self._movements)
               and self._movements[self._movement_index].at_ms <= self._now_ms):
            self._book = self._movements[self._movement_index].book
            self._movement_index += 1

    def _next_rejection(self, request: object) -> str | None:
        if self._reject_index < len(self.rejections.reject_next):
            reason = self.rejections.reject_next[self._reject_index]
            self._reject_index += 1
            return reason
        if isinstance(request, OrderRequest):
            if isinstance(request.symbol, str) and request.symbol in self.rejections.rejected_symbols:
                return "policy_rejected_symbol"
            if (isinstance(request.client_order_id, str)
                    and request.client_order_id in self.rejections.rejected_client_order_ids):
                return "policy_rejected_client_order_id"
            if (isinstance(request.quantity, Decimal)
                    and request.quantity.is_finite()
                    and self.rejections.max_order_quantity is not None
                    and request.quantity > self.rejections.max_order_quantity):
                return "policy_order_quantity_limit"
        return None

    def _unsupported_request_reason(self, request: object) -> str | None:
        if not isinstance(request, OrderRequest):
            return "unsupported_order_request"
        if not isinstance(request.symbol, str) or request.symbol != self.market.symbol:
            return "unsupported_symbol"
        if request.side not in (Side.BUY, Side.SELL):
            return "unsupported_side"
        if request.kind not in (OrderKind.MARKET, OrderKind.LIMIT):
            return "unsupported_order_kind"
        if isinstance(request.quantity, bool) or not isinstance(request.quantity, Decimal):
            return "invalid_quantity"
        try:
            quantity = _as_decimal(request.quantity, "quantity")
        except ValueError:
            return "invalid_quantity"
        if quantity <= _ZERO:
            return "invalid_quantity"
        if request.reduce_only:
            return "unsupported_reduce_only"
        if request.kind is OrderKind.LIMIT:
            if request.price is None or not isinstance(request.price, Decimal):
                return "invalid_limit_price"
            try:
                price = _as_decimal(request.price, "price")
            except ValueError:
                return "invalid_limit_price"
            if price <= _ZERO:
                return "invalid_limit_price"
            if request.client_order_id is not None and (
                not isinstance(request.client_order_id, str) or not request.client_order_id.strip()
            ):
                return "invalid_client_order_id"
            if request.time_in_force not in (None, "GTC", "IOC", "FOK"):
                return "unsupported_time_in_force"
            if request.post_only and request.time_in_force in ("IOC", "FOK"):
                return "unsupported_post_only_time_in_force"
            if request.post_only and self._would_cross(request):
                return "post_only_would_take"
        else:
            if request.price is not None or request.post_only:
                return "unsupported_market_order_options"
            if request.time_in_force not in (None, "IOC", "FOK"):
                return "unsupported_time_in_force"
            if request.client_order_id is not None and (
                not isinstance(request.client_order_id, str) or not request.client_order_id.strip()
            ):
                return "invalid_client_order_id"
        return None

    def _reservation(self, request: OrderRequest) -> tuple[str, Decimal]:
        if request.side is Side.SELL:
            return self.market.base.upper(), _ONE
        if request.kind is OrderKind.LIMIT:
            assert request.price is not None
            price = request.price
        else:
            levels = self._book.asks
            price = levels[-1].price
            impact_multiplier = _ONE + self.impact.impact_bps_per_depth_fraction / _BPS
            price *= impact_multiplier
        rate = max(self.fees.maker_rate, self.fees.taker_rate)
        return self.market.quote.upper(), price * (_ONE + rate)

    def _plan_fills(
        self, request: OrderRequest, quantity: Decimal, *, maker: bool
    ) -> tuple[_PlannedFill, ...]:
        levels = self._book.asks if request.side is Side.BUY else self._book.bids
        visible_quote = sum((level.price * level.quantity for level in levels), _ZERO)
        if visible_quote <= _ZERO:
            return ()
        remaining = quantity
        consumed_quote = _ZERO
        planned: list[_PlannedFill] = []
        per_call = self.max_fill_quantity_per_call or remaining
        for level in levels:
            take = min(level.quantity, remaining, per_call)
            if take <= _ZERO:
                continue
            raw_quote = level.price * take
            fraction = (consumed_quote + raw_quote) / visible_quote
            price = self.impact.adjusted_price(request.side, level.price, fraction)
            if request.kind is OrderKind.LIMIT:
                assert request.price is not None
                if request.side is Side.BUY and price > request.price:
                    break
                if request.side is Side.SELL and price < request.price:
                    break
            planned.append(_PlannedFill(take, price, level.price))
            remaining -= take
            per_call -= take
            consumed_quote += raw_quote
            if remaining <= _ZERO or per_call <= _ZERO:
                break
        return tuple(planned)

    def _apply_planned_fills(
        self, row: _OpenOrder, plan: tuple[_PlannedFill, ...], *, maker: bool
    ) -> tuple[PaperFill, ...]:
        created: list[PaperFill] = []
        for item in plan:
            quote_amount = item.quantity * item.price
            fee = quote_amount * self.fees.rate(maker=maker)
            reserve_release = min(row.reserved_remaining, row.reservation_per_unit * item.quantity)
            asset = row.reserved_asset
            self._balances[asset][1] -= reserve_release
            row.reserved_remaining -= reserve_release
            if row.order.side is Side.BUY:
                spend = quote_amount + fee
                self._balances[self.market.quote.upper()][0] += reserve_release - spend
                self._balances[self.market.base.upper()][0] += item.quantity
            else:
                self._balances[asset][0] += reserve_release
                self._balances[self.market.base.upper()][0] -= item.quantity
                self._balances[self.market.quote.upper()][0] += quote_amount - fee
            fill = PaperFill(
                f"paper-fill-{self._next_fill:08d}",
                row.order.order_id,
                row.order.symbol,
                row.order.side,
                item.quantity,
                item.price,
                quote_amount,
                fee,
                maker,
                self._now_ms,
            )
            self._next_fill += 1
            self._fills.append(fill)
            created.append(fill)
            self._consume_book(row.order.side, item.book_price, item.quantity)
            old_filled = row.order.filled
            new_filled = old_filled + item.quantity
            average = ((row.order.average_price or _ZERO) * old_filled + quote_amount) / new_filled
            row.order = Order(
                row.order.order_id, row.order.symbol, row.order.side, row.order.kind,
                OrderState.PARTIALLY_FILLED, row.order.quantity, new_filled,
                row.order.price, average, row.order.client_order_id, self._now_ms,
            )
        return tuple(created)

    def _finish(self, row: _OpenOrder, state: OrderState) -> None:
        if row.reserved_remaining > _ZERO:
            self._balances[row.reserved_asset][1] -= row.reserved_remaining
            self._balances[row.reserved_asset][0] += row.reserved_remaining
            row.reserved_remaining = _ZERO
        row.order = self._with_state(row.order, state)

    def _consume_book(self, side: Side, price: Decimal, quantity: Decimal) -> None:
        bids = list(self._book.bids)
        asks = list(self._book.asks)
        levels = asks if side is Side.BUY else bids
        for index, level in enumerate(levels):
            if level.price != price:
                continue
            remaining = level.quantity - quantity
            if remaining < _ZERO:
                raise RuntimeError("planned fill exceeds simulated book liquidity")
            if remaining == _ZERO:
                del levels[index]
            else:
                levels[index] = DepthLevel(level.price, remaining)
            self._book = OrderBook(
                self._book.symbol, tuple(bids), tuple(asks),
                self._book.timestamp_ms, self._book.sequence,
            )
            return
        raise RuntimeError("planned fill level disappeared before accounting")

    def _new_order(self, request: object, state: OrderState) -> Order:
        if isinstance(request, OrderRequest):
            symbol = request.symbol if isinstance(request.symbol, str) else self.market.symbol
            side = request.side if isinstance(request.side, Side) else Side.BUY
            kind = request.kind if isinstance(request.kind, OrderKind) else OrderKind.MARKET
            try:
                quantity = _as_decimal(request.quantity, "quantity")
                if quantity <= _ZERO:
                    quantity = _ZERO
            except ValueError:
                quantity = _ZERO
            price = request.price if isinstance(request.price, Decimal) else None
            client_id = request.client_order_id if isinstance(request.client_order_id, str) else None
        else:
            symbol, side, kind, quantity, price, client_id = (
                self.market.symbol, Side.BUY, OrderKind.MARKET, _ZERO, None, None
            )
        order = Order(
            f"paper-order-{self._next_order:08d}", symbol, side, kind, state,
            quantity, price=price, client_order_id=client_id, timestamp_ms=self._now_ms,
        )
        self._next_order += 1
        return order

    @staticmethod
    def _with_state(order: Order, state: OrderState) -> Order:
        return Order(
            order.order_id, order.symbol, order.side, order.kind, state, order.quantity,
            order.filled, order.price, order.average_price, order.client_order_id, order.timestamp_ms,
        )

    def _unknown_order(self, order_id: str) -> Order:
        return Order(
            order_id, self.market.symbol, Side.BUY, OrderKind.MARKET,
            OrderState.UNKNOWN, _ZERO, timestamp_ms=self._now_ms,
        )

    def _result(
        self,
        order: Order,
        accepted: bool,
        reason: str | None,
        started: int,
        fills: tuple[PaperFill, ...],
        network_cost: Decimal = _ZERO,
    ) -> PaperOrderResult:
        return PaperOrderResult(
            order, fills, accepted, reason, self._now_ms - started, network_cost
        )
