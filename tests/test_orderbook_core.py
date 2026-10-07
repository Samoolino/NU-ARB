from decimal import Decimal
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.orderbook_core import OrderBookCore, OrderBookPolicy  # noqa: E402
from arbx.venue_contract import (  # noqa: E402
    BookApplyStatus,
    BookFeedState,
    DepthLevel,
    OrderBookDelta,
    OrderBookSnapshot,
    Side,
    TradePrint,
)

VENUE = "venue-x"
SYMBOL = "BTC/USD"
MONO = 10_000_000_000
WALL = 1_800_000_000_000
D = Decimal


def snapshot(*, sequence=100, source_timestamp_ms=WALL):
    return OrderBookSnapshot(
        VENUE,
        SYMBOL,
        (DepthLevel(D("100"), D("2")), DepthLevel(D("99"), D("3"))),
        (DepthLevel(D("101"), D("1")), DepthLevel(D("102"), D("4"))),
        sequence,
        source_timestamp_ms,
    )


def connected_core(policy=None, *, monotonic_ns=MONO):
    core = OrderBookCore(VENUE, SYMBOL, policy or OrderBookPolicy())
    assert core.begin_connect(monotonic_ns)
    assert core.mark_connected(monotonic_ns)
    return core


def apply_snapshot(core, *, at=MONO, wall=WALL, value=None):
    return core.apply_snapshot(value or snapshot(), at, wall)


def test_rest_snapshot_bootstraps_and_sequence_delta_updates_book():
    core = connected_core()
    assert apply_snapshot(core).status is BookApplyStatus.APPLIED
    delta = OrderBookDelta(
        VENUE,
        SYMBOL,
        101,
        101,
        bids=(DepthLevel(D("100"), D("1.5")),),
        asks=(DepthLevel(D("102"), D("0")), DepthLevel(D("101.5"), D("2"))),
        previous_sequence=100,
        source_timestamp_ms=WALL + 100,
    )

    result = core.apply_delta(delta, MONO + 100_000_000, WALL + 100)

    assert result.status is BookApplyStatus.APPLIED
    assert result.sequence == 101
    view = core.current_view(MONO + 100_000_000, WALL + 100)
    assert view is not None
    assert view.bids == (
        DepthLevel(D("100"), D("1.5")),
        DepthLevel(D("99"), D("3")),
    )
    assert view.asks == (
        DepthLevel(D("101"), D("1")),
        DepthLevel(D("101.5"), D("2")),
    )


def test_sequence_gap_fails_closed_and_requires_a_new_snapshot():
    core = connected_core()
    apply_snapshot(core)
    gap = OrderBookDelta(
        VENUE,
        SYMBOL,
        102,
        102,
        bids=(DepthLevel(D("100"), D("1")),),
        previous_sequence=100,
        source_timestamp_ms=WALL + 100,
    )

    result = core.apply_delta(gap, MONO + 100_000_000, WALL + 100)

    assert result.status is BookApplyStatus.RESYNC_REQUIRED
    assert result.reason == "sequence_gap"
    assert core.current_view(MONO + 100_000_000, WALL + 100) is None
    assert core.apply_delta(gap, MONO + 200_000_000, WALL + 200).reason == "snapshot_required"


def test_out_of_order_delta_also_requires_resynchronization():
    core = connected_core()
    apply_snapshot(core)
    duplicate = OrderBookDelta(
        VENUE,
        SYMBOL,
        100,
        100,
        previous_sequence=100,
        source_timestamp_ms=WALL + 1,
    )
    result = core.apply_delta(duplicate, MONO + 1, WALL + 1)
    assert result.status is BookApplyStatus.RESYNC_REQUIRED
    assert result.reason == "out_of_order_delta"


def test_source_age_and_clock_skew_are_rejected_separately():
    policy = OrderBookPolicy(max_source_age_ms=1_000, max_clock_skew_ms=5_000)
    core = connected_core(policy)
    stale = apply_snapshot(core, wall=WALL, value=snapshot(source_timestamp_ms=WALL - 2_000))
    assert stale.status is BookApplyStatus.REJECTED
    assert stale.reason == "source_stale"

    drift = apply_snapshot(
        core,
        wall=WALL,
        value=snapshot(source_timestamp_ms=WALL - 6_000),
    )
    assert drift.status is BookApplyStatus.REJECTED
    assert drift.reason == "source_clock_skew"

    assert apply_snapshot(core).status is BookApplyStatus.APPLIED
    freshness = core.freshness(MONO + 1, WALL + 2_000)
    assert not freshness.current
    assert freshness.reason == "source_stale"
    assert core.current_view(MONO + 1, WALL + 2_000) is None
    receive_stale = core.freshness(
        MONO + policy.max_receive_age_ns + 1,
        WALL,
    )
    assert not receive_stale.current
    assert receive_stale.reason == "receive_stale"


def test_depth_vwap_slippage_imbalance_microprice_and_trade_velocity():
    policy = OrderBookPolicy(metric_depth_levels=2, trade_window_ns=10_000_000_000)
    core = connected_core(policy)
    apply_snapshot(core)
    delta = OrderBookDelta(
        VENUE,
        SYMBOL,
        101,
        101,
        bids=(DepthLevel(D("100"), D("1.5")),),
        asks=(DepthLevel(D("101"), D("1")), DepthLevel(D("102"), D("2"))),
        previous_sequence=100,
        source_timestamp_ms=WALL + 100,
    )
    core.apply_delta(delta, MONO + 100, WALL + 100)

    assert core.depth_quote(Side.BUY, MONO + 100, WALL + 100) == D("305")
    assert core.depth_quote(Side.SELL, MONO + 100, WALL + 100) == D("447")
    buy = core.estimate_vwap(Side.BUY, D("2"), MONO + 100, WALL + 100)
    assert buy is not None and buy.complete
    assert buy.quote_quantity == D("203")
    assert buy.average_price == D("101.5")
    assert buy.worst_price == D("102")
    assert buy.slippage_bps == (D("101.5") / D("101") - 1) * D("10000")
    partial = core.estimate_vwap(Side.SELL, D("10"), MONO + 100, WALL + 100)
    assert partial is not None and not partial.complete
    assert partial.filled_quantity == D("4.5")

    assert core.record_trade(
        TradePrint(VENUE, SYMBOL, Side.BUY, D("101"), D("0.5"), WALL + 200),
        MONO + 200,
        WALL + 200,
    )
    assert core.record_trade(
        TradePrint(VENUE, SYMBOL, Side.SELL, D("100"), D("0.25"), WALL + 300),
        MONO + 300,
        WALL + 300,
    )
    metrics = core.metrics(MONO + 300, WALL + 300)
    assert metrics is not None
    assert metrics.best_bid == D("100")
    assert metrics.best_ask == D("101")
    assert metrics.spread == D("1")
    assert metrics.bid_depth_quote == D("447")
    assert metrics.ask_depth_quote == D("305")
    assert metrics.imbalance == D(142) / D(752)
    assert metrics.microprice == (D("101") * D("1.5") + D("100")) / D("2.5")
    assert metrics.trade_velocity.trade_count == 2
    assert metrics.trade_velocity.buy_quantity == D("0.5")
    assert metrics.trade_velocity.sell_quantity == D("0.25")
    assert metrics.trade_velocity.base_quantity_per_second == D("0.075")


def test_heartbeat_loss_backoff_and_recovery_require_fresh_snapshot():
    policy = OrderBookPolicy(
        reconnect_base_ns=200,
        reconnect_max_ns=500,
        heartbeat_timeout_ns=100,
    )
    core = connected_core(policy)
    apply_snapshot(core)
    assert core.current_view(MONO, WALL) is not None
    assert core.check_heartbeat(MONO + 100)
    assert not core.check_heartbeat(MONO + 101)
    assert core.feed_status.state is BookFeedState.BACKOFF
    assert core.feed_status.reconnect_at_ns == MONO + 301
    assert core.current_view(MONO + 101, WALL + 101) is None
    assert not core.begin_reconnect(MONO + 300)
    assert core.begin_reconnect(MONO + 301)
    assert core.mark_connected(MONO + 302)

    delta = OrderBookDelta(
        VENUE,
        SYMBOL,
        101,
        101,
        previous_sequence=100,
        source_timestamp_ms=WALL + 302,
    )
    assert core.apply_delta(delta, MONO + 302, WALL + 302).reason == "snapshot_required"
    assert apply_snapshot(
        core,
        at=MONO + 303,
        wall=WALL + 303,
        value=snapshot(sequence=200, source_timestamp_ms=WALL + 303),
    ).status is BookApplyStatus.APPLIED
    assert core.current_view(MONO + 303, WALL + 303) is not None
    assert core.feed_status.state is BookFeedState.CONNECTED


def test_reconnect_backoff_is_exponential_and_explicit_disconnect_is_not_current():
    policy = OrderBookPolicy(reconnect_base_ns=100, reconnect_max_ns=250)
    core = connected_core(policy)
    apply_snapshot(core)
    core.feed_lost(MONO + 1, "socket_closed")
    assert core.feed_status.reconnect_at_ns == MONO + 101
    assert core.begin_reconnect(MONO + 101)
    core.connection_failed(MONO + 102, "connect_error")
    assert core.feed_status.state is BookFeedState.BACKOFF
    assert core.feed_status.consecutive_failures == 2
    assert core.feed_status.reconnect_at_ns == MONO + 302
    assert core.begin_reconnect(MONO + 302)
    assert core.mark_connected(MONO + 303)
    assert core.feed_status.consecutive_failures == 0
    core.disconnect(MONO + 304, "operator_disconnect")
    assert core.feed_status.state is BookFeedState.DISCONNECTED
    assert core.current_view(MONO + 304, WALL + 304) is None


def test_invalid_or_stale_snapshot_is_not_exposed_as_current():
    core = connected_core()
    crossed = OrderBookSnapshot(
        VENUE,
        SYMBOL,
        (DepthLevel(D("101"), D("1")),),
        (DepthLevel(D("101"), D("1")),),
        1,
        WALL,
    )
    assert apply_snapshot(core, value=crossed).reason == "invalid_snapshot_spread"
    assert core.current_view(MONO, WALL) is None

    with pytest.raises(ValueError):
        OrderBookPolicy(max_receive_age_ns=0)
