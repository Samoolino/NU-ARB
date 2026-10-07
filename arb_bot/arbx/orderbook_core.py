"""Deterministic, stateful public order-book maintenance.

This module only transforms caller-supplied snapshots, deltas, trades, and
clock readings. It performs no I/O and grants no execution permission.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Deque, Mapping

from .venue_contract import (
    BookApplyStatus,
    BookFeedState,
    DepthLevel,
    OrderBookDelta,
    OrderBookSnapshot,
    Side,
    TradePrint,
)

_ZERO = Decimal(0)
_TEN_THOUSAND = Decimal(10_000)
_NANOSECONDS_PER_SECOND = Decimal(1_000_000_000)


@dataclass(frozen=True, slots=True)
class OrderBookPolicy:
    max_receive_age_ns: int = 2_000_000_000
    max_source_age_ms: int = 5_000
    max_clock_skew_ms: int = 1_000
    heartbeat_timeout_ns: int = 5_000_000_000
    reconnect_base_ns: int = 250_000_000
    reconnect_max_ns: int = 30_000_000_000
    metric_depth_levels: int = 10
    trade_window_ns: int = 60_000_000_000

    def __post_init__(self) -> None:
        positive = (
            self.max_receive_age_ns,
            self.max_source_age_ms,
            self.max_clock_skew_ms,
            self.heartbeat_timeout_ns,
            self.reconnect_base_ns,
            self.reconnect_max_ns,
            self.metric_depth_levels,
            self.trade_window_ns,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("order-book policy values must be positive")
        if self.reconnect_max_ns < self.reconnect_base_ns:
            raise ValueError("reconnect_max_ns must be >= reconnect_base_ns")


@dataclass(frozen=True, slots=True)
class BookFreshness:
    current: bool
    reason: str
    receive_age_ns: int | None = None
    source_age_ms: int | None = None
    clock_skew_ms: int | None = None


@dataclass(frozen=True, slots=True)
class BookFeedStatus:
    state: BookFeedState
    consecutive_failures: int
    reconnect_at_ns: int | None
    last_heartbeat_ns: int | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class BookApplyResult:
    status: BookApplyStatus
    reason: str
    sequence: int | None


@dataclass(frozen=True, slots=True)
class OrderBookView:
    venue_id: str
    symbol: str
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    sequence: int
    source_timestamp_ms: int
    received_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class VwapEstimate:
    side: Side
    requested_quantity: Decimal
    filled_quantity: Decimal
    quote_quantity: Decimal
    average_price: Decimal | None
    worst_price: Decimal | None
    slippage_bps: Decimal | None
    complete: bool


@dataclass(frozen=True, slots=True)
class TradeVelocity:
    trade_count: int
    base_quantity_per_second: Decimal
    quote_quantity_per_second: Decimal
    buy_quantity: Decimal
    sell_quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookMetrics:
    best_bid: Decimal
    best_ask: Decimal
    spread: Decimal
    spread_bps: Decimal
    bid_depth_quote: Decimal
    ask_depth_quote: Decimal
    imbalance: Decimal
    microprice: Decimal
    trade_velocity: TradeVelocity


@dataclass(frozen=True, slots=True)
class _ReceivedTrade:
    trade: TradePrint
    received_monotonic_ns: int


class OrderBookCore:
    """One-market book state with strict sequence and freshness fail-closed rules.

    All times are supplied by the caller: monotonic values are nanoseconds and
    source/local wall-clock values are Unix milliseconds. Feed transitions are
    explicit; a reconnect always requires a new snapshot before deltas can be
    trusted. A retained level image after feed loss is never reported current.
    """

    def __init__(
        self,
        venue_id: str,
        symbol: str,
        policy: OrderBookPolicy = OrderBookPolicy(),
    ) -> None:
        if not venue_id or not symbol:
            raise ValueError("venue_id and symbol are required")
        self.venue_id = venue_id
        self.symbol = symbol
        self.policy = policy
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._sequence: int | None = None
        self._source_timestamp_ms: int | None = None
        self._received_monotonic_ns: int | None = None
        self._receipt_clock_skew_ms: int | None = None
        self._needs_snapshot = True
        self._state = BookFeedState.DISCONNECTED
        self._failures = 0
        self._reconnect_at_ns: int | None = None
        self._last_heartbeat_ns: int | None = None
        self._feed_reason: str | None = None
        self._trades: Deque[_ReceivedTrade] = deque()
        self._last_trade_receive_ns: int | None = None

    @property
    def feed_status(self) -> BookFeedStatus:
        return BookFeedStatus(
            state=self._state,
            consecutive_failures=self._failures,
            reconnect_at_ns=self._reconnect_at_ns,
            last_heartbeat_ns=self._last_heartbeat_ns,
            reason=self._feed_reason,
        )

    @property
    def sequence(self) -> int | None:
        return self._sequence

    def begin_connect(self, now_monotonic_ns: int) -> bool:
        if now_monotonic_ns < 0 or self._state is not BookFeedState.DISCONNECTED:
            return False
        self._state = BookFeedState.CONNECTING
        self._reconnect_at_ns = None
        self._feed_reason = None
        return True

    def begin_reconnect(self, now_monotonic_ns: int) -> bool:
        if not self.reconnect_due(now_monotonic_ns):
            return False
        self._state = BookFeedState.CONNECTING
        self._reconnect_at_ns = None
        self._feed_reason = None
        return True

    def mark_connected(self, now_monotonic_ns: int) -> bool:
        if now_monotonic_ns < 0 or self._state is not BookFeedState.CONNECTING:
            return False
        self._state = BookFeedState.CONNECTED
        self._last_heartbeat_ns = now_monotonic_ns
        self._failures = 0
        self._reconnect_at_ns = None
        self._feed_reason = None
        return True

    def disconnect(self, now_monotonic_ns: int, reason: str = "disconnected") -> None:
        self._state = BookFeedState.DISCONNECTED
        self._last_heartbeat_ns = None
        self._reconnect_at_ns = None
        self._feed_reason = reason
        self._needs_snapshot = True

    def feed_lost(self, now_monotonic_ns: int, reason: str = "feed_lost") -> None:
        self._schedule_backoff(now_monotonic_ns, reason)

    def connection_failed(self, now_monotonic_ns: int, reason: str = "connect_failed") -> None:
        self._schedule_backoff(now_monotonic_ns, reason)

    def _schedule_backoff(self, now_monotonic_ns: int, reason: str) -> None:
        if now_monotonic_ns < 0:
            raise ValueError("monotonic time must be non-negative")
        self._failures += 1
        exponent = min(self._failures - 1, 62)
        delay = min(
            self.policy.reconnect_base_ns * (2**exponent),
            self.policy.reconnect_max_ns,
        )
        self._state = BookFeedState.BACKOFF
        self._last_heartbeat_ns = None
        self._reconnect_at_ns = now_monotonic_ns + delay
        self._feed_reason = reason
        self._needs_snapshot = True

    def reconnect_due(self, now_monotonic_ns: int) -> bool:
        return (
            self._state is BookFeedState.BACKOFF
            and self._reconnect_at_ns is not None
            and now_monotonic_ns >= self._reconnect_at_ns
        )

    def heartbeat(self, now_monotonic_ns: int) -> bool:
        if self._state is not BookFeedState.CONNECTED or now_monotonic_ns < 0:
            return False
        if (
            self._last_heartbeat_ns is not None
            and now_monotonic_ns < self._last_heartbeat_ns
        ):
            self.feed_lost(now_monotonic_ns, "heartbeat_out_of_order")
            return False
        self._last_heartbeat_ns = now_monotonic_ns
        return True

    def check_heartbeat(self, now_monotonic_ns: int) -> bool:
        """Return true while healthy; an expired heartbeat schedules backoff."""
        if self._state is not BookFeedState.CONNECTED:
            return False
        if self._last_heartbeat_ns is None or now_monotonic_ns < self._last_heartbeat_ns:
            self.feed_lost(now_monotonic_ns, "heartbeat_clock_invalid")
            return False
        if now_monotonic_ns - self._last_heartbeat_ns > self.policy.heartbeat_timeout_ns:
            self.feed_lost(now_monotonic_ns, "heartbeat_timeout")
            return False
        return True

    def apply_snapshot(
        self,
        snapshot: OrderBookSnapshot,
        received_monotonic_ns: int,
        received_wall_time_ms: int,
    ) -> BookApplyResult:
        if not self._matches(snapshot.venue_id, snapshot.symbol):
            return self._result(BookApplyStatus.REJECTED, "market_mismatch")
        error = self._time_error(
            snapshot.source_timestamp_ms, received_monotonic_ns, received_wall_time_ms
        )
        candidate_bids = self._snapshot_levels(snapshot.bids, bids=True)
        candidate_asks = self._snapshot_levels(snapshot.asks, bids=False)
        if snapshot.sequence < 0 or error or candidate_bids is None or candidate_asks is None:
            reason = error or (
                "invalid_snapshot_levels"
                if candidate_bids is None or candidate_asks is None
                else "invalid_snapshot_sequence"
            )
            self._invalidate()
            return self._result(BookApplyStatus.REJECTED, reason)
        if not self._valid_spread(candidate_bids, candidate_asks):
            self._invalidate()
            return self._result(BookApplyStatus.REJECTED, "invalid_snapshot_spread")
        if (
            self._received_monotonic_ns is not None
            and received_monotonic_ns < self._received_monotonic_ns
        ):
            self._invalidate()
            return self._result(BookApplyStatus.REJECTED, "receive_time_out_of_order")
        self._bids = candidate_bids
        self._asks = candidate_asks
        self._sequence = snapshot.sequence
        self._source_timestamp_ms = snapshot.source_timestamp_ms
        self._received_monotonic_ns = received_monotonic_ns
        self._receipt_clock_skew_ms = received_wall_time_ms - snapshot.source_timestamp_ms
        self._needs_snapshot = False
        return self._result(BookApplyStatus.APPLIED, "snapshot_applied")

    def apply_delta(
        self,
        delta: OrderBookDelta,
        received_monotonic_ns: int,
        received_wall_time_ms: int,
    ) -> BookApplyResult:
        if not self._matches(delta.venue_id, delta.symbol):
            return self._result(BookApplyStatus.REJECTED, "market_mismatch")
        if self._state is not BookFeedState.CONNECTED:
            self._invalidate()
            return self._result(BookApplyStatus.RESYNC_REQUIRED, "feed_not_connected")
        if self._needs_snapshot or self._sequence is None:
            self._invalidate()
            return self._result(BookApplyStatus.RESYNC_REQUIRED, "snapshot_required")
        if (
            self._received_monotonic_ns is not None
            and received_monotonic_ns < self._received_monotonic_ns
        ):
            return self._resync("receive_time_out_of_order")
        if (
            self._source_timestamp_ms is not None
            and delta.source_timestamp_ms < self._source_timestamp_ms
        ):
            return self._resync("source_timestamp_out_of_order")
        time_error = self._time_error(
            delta.source_timestamp_ms, received_monotonic_ns, received_wall_time_ms
        )
        if time_error:
            return self._resync(time_error)
        if delta.first_sequence < 0 or delta.last_sequence < delta.first_sequence:
            return self._resync("invalid_delta_sequence")
        if delta.last_sequence <= self._sequence:
            return self._resync("out_of_order_delta")
        bridges_next = delta.first_sequence <= self._sequence + 1 <= delta.last_sequence
        if delta.previous_sequence is not None:
            if delta.previous_sequence != self._sequence or not bridges_next:
                return self._resync("sequence_gap")
        elif not bridges_next:
            return self._resync(
                "sequence_gap"
                if delta.first_sequence > self._sequence + 1
                else "out_of_order_delta"
            )

        bids = dict(self._bids)
        asks = dict(self._asks)
        if not self._apply_levels(bids, delta.bids) or not self._apply_levels(asks, delta.asks):
            return self._resync("invalid_delta_levels")
        if not self._valid_spread(bids, asks):
            return self._resync("invalid_delta_spread")
        self._bids = bids
        self._asks = asks
        self._sequence = delta.last_sequence
        self._source_timestamp_ms = delta.source_timestamp_ms
        self._received_monotonic_ns = received_monotonic_ns
        self._receipt_clock_skew_ms = received_wall_time_ms - delta.source_timestamp_ms
        return self._result(BookApplyStatus.APPLIED, "delta_applied")

    def freshness(
        self, now_monotonic_ns: int, now_wall_time_ms: int
    ) -> BookFreshness:
        if self._state is not BookFeedState.CONNECTED:
            return BookFreshness(False, "feed_disconnected")
        if self._needs_snapshot or self._sequence is None:
            return BookFreshness(False, "snapshot_required")
        if self._received_monotonic_ns is None or self._source_timestamp_ms is None:
            return BookFreshness(False, "snapshot_required")
        receive_age = now_monotonic_ns - self._received_monotonic_ns
        source_age = now_wall_time_ms - self._source_timestamp_ms
        skew = self._receipt_clock_skew_ms
        if receive_age < 0:
            return BookFreshness(False, "monotonic_clock_regressed", receive_age, source_age, skew)
        if receive_age > self.policy.max_receive_age_ns:
            return BookFreshness(False, "receive_stale", receive_age, source_age, skew)
        if skew is None or abs(skew) > self.policy.max_clock_skew_ms:
            return BookFreshness(False, "source_clock_skew", receive_age, source_age, skew)
        if source_age > self.policy.max_source_age_ms:
            return BookFreshness(False, "source_stale", receive_age, source_age, skew)
        if source_age < -self.policy.max_clock_skew_ms:
            return BookFreshness(False, "source_timestamp_in_future", receive_age, source_age, skew)
        if (
            self._last_heartbeat_ns is None
            or now_monotonic_ns < self._last_heartbeat_ns
            or now_monotonic_ns - self._last_heartbeat_ns > self.policy.heartbeat_timeout_ns
        ):
            return BookFreshness(False, "heartbeat_stale", receive_age, source_age, skew)
        return BookFreshness(True, "current", receive_age, source_age, skew)

    def current_view(
        self, now_monotonic_ns: int, now_wall_time_ms: int
    ) -> OrderBookView | None:
        if not self.freshness(now_monotonic_ns, now_wall_time_ms).current:
            return None
        assert self._sequence is not None
        assert self._source_timestamp_ms is not None
        assert self._received_monotonic_ns is not None
        return OrderBookView(
            self.venue_id,
            self.symbol,
            self._ordered_levels(self._bids, bids=True),
            self._ordered_levels(self._asks, bids=False),
            self._sequence,
            self._source_timestamp_ms,
            self._received_monotonic_ns,
        )

    def depth_quote(
        self,
        side: Side,
        now_monotonic_ns: int,
        now_wall_time_ms: int,
        levels: int | None = None,
    ) -> Decimal | None:
        view = self.current_view(now_monotonic_ns, now_wall_time_ms)
        if view is None:
            return None
        if levels is not None and levels <= 0:
            raise ValueError("levels must be positive")
        visible = view.asks if side is Side.BUY else view.bids
        visible = visible[:levels] if levels is not None else visible
        return sum((level.price * level.quantity for level in visible), _ZERO)

    def estimate_vwap(
        self,
        side: Side,
        quantity: Decimal,
        now_monotonic_ns: int,
        now_wall_time_ms: int,
    ) -> VwapEstimate | None:
        if quantity <= _ZERO:
            raise ValueError("quantity must be positive")
        view = self.current_view(now_monotonic_ns, now_wall_time_ms)
        if view is None:
            return None
        levels = view.asks if side is Side.BUY else view.bids
        remaining = quantity
        filled = quote = _ZERO
        worst: Decimal | None = None
        for level in levels:
            take = min(remaining, level.quantity)
            if take <= _ZERO:
                continue
            filled += take
            quote += take * level.price
            remaining -= take
            worst = level.price
            if remaining == _ZERO:
                break
        average = quote / filled if filled else None
        best = levels[0].price if levels else None
        if average is None or best is None:
            slippage = None
        elif side is Side.BUY:
            slippage = (average / best - 1) * _TEN_THOUSAND
        else:
            slippage = (1 - average / best) * _TEN_THOUSAND
        return VwapEstimate(
            side, quantity, filled, quote, average, worst, slippage, remaining == _ZERO
        )

    def record_trade(
        self,
        trade: TradePrint,
        received_monotonic_ns: int,
        received_wall_time_ms: int,
    ) -> bool:
        if (
            not self._matches(trade.venue_id, trade.symbol)
            or self._state is not BookFeedState.CONNECTED
            or trade.price <= _ZERO
            or trade.quantity <= _ZERO
            or received_monotonic_ns < 0
        ):
            return False
        if (
            self._last_trade_receive_ns is not None
            and received_monotonic_ns < self._last_trade_receive_ns
        ):
            return False
        if self._time_error(trade.source_timestamp_ms, received_monotonic_ns, received_wall_time_ms):
            return False
        self._trades.append(_ReceivedTrade(trade, received_monotonic_ns))
        self._last_trade_receive_ns = received_monotonic_ns
        self._prune_trades(received_monotonic_ns)
        return True

    def metrics(
        self, now_monotonic_ns: int, now_wall_time_ms: int
    ) -> OrderBookMetrics | None:
        view = self.current_view(now_monotonic_ns, now_wall_time_ms)
        if view is None:
            return None
        bid = view.bids[0]
        ask = view.asks[0]
        bid_depth = sum(
            (level.price * level.quantity for level in view.bids[: self.policy.metric_depth_levels]),
            _ZERO,
        )
        ask_depth = sum(
            (level.price * level.quantity for level in view.asks[: self.policy.metric_depth_levels]),
            _ZERO,
        )
        total_depth = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total_depth if total_depth else _ZERO
        top_quantity = bid.quantity + ask.quantity
        microprice = (
            (ask.price * bid.quantity + bid.price * ask.quantity) / top_quantity
            if top_quantity
            else (bid.price + ask.price) / 2
        )
        spread = ask.price - bid.price
        midpoint = (ask.price + bid.price) / 2
        return OrderBookMetrics(
            bid.price,
            ask.price,
            spread,
            spread / midpoint * _TEN_THOUSAND if midpoint else _ZERO,
            bid_depth,
            ask_depth,
            imbalance,
            microprice,
            self._trade_velocity(now_monotonic_ns),
        )

    def _trade_velocity(self, now_monotonic_ns: int) -> TradeVelocity:
        self._prune_trades(now_monotonic_ns)
        buy = sell = quote = _ZERO
        count = 0
        for item in self._trades:
            quantity = item.trade.quantity
            count += 1
            quote += item.trade.price * quantity
            if item.trade.side is Side.BUY:
                buy += quantity
            else:
                sell += quantity
        base_total = buy + sell
        seconds = Decimal(self.policy.trade_window_ns) / _NANOSECONDS_PER_SECOND
        return TradeVelocity(
            count,
            base_total / seconds,
            quote / seconds,
            buy,
            sell,
        )

    def _prune_trades(self, now_monotonic_ns: int) -> None:
        cutoff = now_monotonic_ns - self.policy.trade_window_ns
        while self._trades and self._trades[0].received_monotonic_ns < cutoff:
            self._trades.popleft()

    def _time_error(
        self, source_timestamp_ms: int, received_monotonic_ns: int, received_wall_time_ms: int
    ) -> str | None:
        if received_monotonic_ns < 0 or received_wall_time_ms < 0 or source_timestamp_ms < 0:
            return "invalid_timestamp"
        skew = received_wall_time_ms - source_timestamp_ms
        source_age = received_wall_time_ms - source_timestamp_ms
        if abs(skew) > self.policy.max_clock_skew_ms:
            return "source_clock_skew"
        if source_age > self.policy.max_source_age_ms:
            return "source_stale"
        if source_age < -self.policy.max_clock_skew_ms:
            return "source_timestamp_in_future"
        return None

    def _matches(self, venue_id: str, symbol: str) -> bool:
        return venue_id == self.venue_id and symbol == self.symbol

    @staticmethod
    def _snapshot_levels(
        levels: tuple[DepthLevel, ...], *, bids: bool
    ) -> dict[Decimal, Decimal] | None:
        previous: Decimal | None = None
        result: dict[Decimal, Decimal] = {}
        for level in levels:
            if (
                not isinstance(level.price, Decimal)
                or not isinstance(level.quantity, Decimal)
                or not level.price.is_finite()
                or not level.quantity.is_finite()
                or level.price <= _ZERO
                or level.quantity <= _ZERO
            ):
                return None
            if previous is not None and (
                (bids and level.price >= previous)
                or (not bids and level.price <= previous)
            ):
                return None
            if level.price in result:
                return None
            result[level.price] = level.quantity
            previous = level.price
        return result if result else None

    @staticmethod
    def _apply_levels(
        book_side: dict[Decimal, Decimal], updates: tuple[DepthLevel, ...]
    ) -> bool:
        for level in updates:
            if (
                not isinstance(level.price, Decimal)
                or not isinstance(level.quantity, Decimal)
                or not level.price.is_finite()
                or not level.quantity.is_finite()
                or level.price <= _ZERO
                or level.quantity < _ZERO
            ):
                return False
            if level.quantity == _ZERO:
                book_side.pop(level.price, None)
            else:
                book_side[level.price] = level.quantity
        return True

    @staticmethod
    def _ordered_levels(
        levels: Mapping[Decimal, Decimal], *, bids: bool
    ) -> tuple[DepthLevel, ...]:
        return tuple(
            DepthLevel(price, levels[price])
            for price in sorted(levels, reverse=bids)
        )

    @staticmethod
    def _valid_spread(
        bids: Mapping[Decimal, Decimal], asks: Mapping[Decimal, Decimal]
    ) -> bool:
        return bool(bids and asks and max(bids) < min(asks))

    def _invalidate(self) -> None:
        self._needs_snapshot = True

    def _resync(self, reason: str) -> BookApplyResult:
        self._invalidate()
        return self._result(BookApplyStatus.RESYNC_REQUIRED, reason)

    def _result(self, status: BookApplyStatus, reason: str) -> BookApplyResult:
        return BookApplyResult(status, reason, self._sequence)
