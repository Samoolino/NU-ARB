from decimal import Decimal as D

import pytest

from arbx.paper_transport import (
    BookMovement,
    CancelFailurePolicy,
    DepthImpactModel,
    FeeModel,
    LatencyModel,
    NetworkCostModel,
    PaperTransport,
    RateLimitModel,
    RejectionPolicy,
    SimulationMode,
)
from arbx.venue_contract import (
    DepthLevel,
    MarketInfo,
    MarketType,
    OrderBook,
    OrderKind,
    OrderRequest,
    OrderState,
    Side,
)


def market(active=True, market_type=MarketType.SPOT):
    return MarketInfo("BTC/USDT", "BTC", "USDT", market_type, active=active)


def book(*, bid=99, ask=101, bid_qty=5, ask_qty=2):
    return OrderBook(
        "BTC/USDT",
        (DepthLevel(D(bid), D(bid_qty)), DepthLevel(D(bid - 1), D(3))),
        (DepthLevel(D(ask), D(ask_qty)), DepthLevel(D(ask + 1), D(3))),
        timestamp_ms=1_000,
        sequence=1,
    )


def make_transport(**kwargs):
    options = {
        "fees": FeeModel(D("0.001"), D("0.01")),
    }
    options.update(kwargs)
    return PaperTransport(
        market(), book(), {"BTC": D(10), "USDT": D(10_000)}, **options
    )


def market_buy(quantity="1"):
    return OrderRequest("BTC/USDT", Side.BUY, OrderKind.MARKET, D(quantity))


def test_depth_impact_fees_network_cost_and_balances_are_deterministic():
    options = {
        "fees": FeeModel(D("0.001"), D("0.01")),
        "impact": DepthImpactModel(D(100)),
        "network_costs": NetworkCostModel(D("0.5")),
        "balances": {"BTC": D(10), "USDT": D(10_000)},
    }
    first = PaperTransport(market(), book(), options["balances"], **{
        key: value for key, value in options.items() if key != "balances"
    })
    second = PaperTransport(market(), book(), options["balances"], **{
        key: value for key, value in options.items() if key != "balances"
    })

    result_a = first.submit(market_buy("3"))
    result_b = second.submit(market_buy("3"))

    assert result_a == result_b
    assert result_a.accepted
    assert result_a.order.state is OrderState.FILLED
    assert len(result_a.fills) == 2
    assert result_a.fills[0].price > D(101)
    assert result_a.fills[1].price > D(102)
    assert all(fill.fee_quote == fill.quote_amount * D("0.01") for fill in result_a.fills)
    assert result_a.network_cost_quote == D("0.5")
    balances = {row.asset: row for row in first.balance_snapshot()}
    spent = sum((fill.quote_amount + fill.fee_quote for fill in result_a.fills), D(0))
    assert balances["BTC"].free == D(13)
    assert balances["USDT"].total == D(10_000) - spent - D("0.5")
    assert balances["USDT"].locked == 0


def test_latency_applies_scheduled_book_movement_before_execution():
    moved_book = book(ask=105, ask_qty=4)
    transport = make_transport(
        latency=LatencyModel(submit_ms=5),
        book_movements=(BookMovement(5, moved_book),),
    )

    result = transport.submit(market_buy())

    assert result.latency_ms == 5
    assert result.order.average_price == D(105)
    assert transport.book.asks[0].quantity == D(3)


def test_rate_limiting_is_virtual_time_based_and_recovers_after_window():
    transport = make_transport(
        latency=LatencyModel(submit_ms=5),
        rate_limit=RateLimitModel(max_requests=1, window_ms=10),
    )

    accepted = transport.submit(market_buy())
    limited = transport.submit(market_buy())
    after_window = transport.submit(market_buy())

    assert accepted.accepted
    assert not limited.accepted and limited.reason == "rate_limited"
    assert after_window.accepted


def test_partial_fills_consume_book_and_cancel_failure_preserves_reservation():
    transport = make_transport(
        max_fill_quantity_per_call=D(1),
        cancel_failures=CancelFailurePolicy(fail_next_count=1),
    )
    request = OrderRequest(
        "BTC/USDT", Side.BUY, OrderKind.LIMIT, D(3), price=D(101), time_in_force="GTC"
    )

    first = transport.submit(request)
    assert first.order.state is OrderState.PARTIALLY_FILLED
    assert first.order.filled == D(1)
    assert transport.book.asks[0].quantity == D(1)

    second = transport.process_order(first.order.order_id)
    assert second.order.filled == D(2)
    assert second.order.state is OrderState.PARTIALLY_FILLED
    assert transport.book.asks[0].price == D(102)
    assert not first.fills[0].maker
    assert second.fills[0].maker
    assert second.fills[0].fee_quote == second.fills[0].quote_amount * D("0.001")

    failed = transport.cancel(first.order.order_id)
    assert not failed.canceled and failed.reason == "cancel_failed"
    assert failed.order.state is OrderState.PARTIALLY_FILLED
    assert next(row for row in transport.balance_snapshot() if row.asset == "USDT").locked > 0

    canceled = transport.cancel(first.order.order_id)
    assert canceled.canceled
    assert canceled.order.state is OrderState.CANCELED
    assert next(row for row in transport.balance_snapshot() if row.asset == "USDT").locked == 0


def test_fok_policy_rejection_and_network_cost_insufficiency_fail_closed():
    transport = make_transport(max_fill_quantity_per_call=D(1))
    fok = OrderRequest(
        "BTC/USDT", Side.BUY, OrderKind.LIMIT, D(2), price=D(101), time_in_force="FOK"
    )
    before_balances, before_book = transport.balance_snapshot(), transport.book

    rejected_fok = transport.submit(fok)
    policy_transport = make_transport(rejections=RejectionPolicy(reject_next=("venue_declined",)))
    rejected_policy = policy_transport.submit(market_buy())
    poor_transport = PaperTransport(
        market(), book(), {"BTC": D(1), "USDT": D("0.25")},
        fees=FeeModel(D(0), D(0)), network_costs=NetworkCostModel(D("0.5")),
    )
    rejected_cost = poor_transport.submit(
        OrderRequest("BTC/USDT", Side.SELL, OrderKind.LIMIT, D(1), price=D(99))
    )

    assert rejected_fok.reason == "fok_not_fully_fillable"
    assert rejected_fok.order.state is OrderState.REJECTED
    assert transport.balance_snapshot() == before_balances
    assert transport.book == before_book
    assert rejected_policy.reason == "venue_declined"
    assert rejected_policy.order.state is OrderState.REJECTED
    assert rejected_cost.reason == "insufficient_balance"


@pytest.mark.parametrize(
    ("order_request", "reason"),
    [
        (OrderRequest("DOGE/USDT", Side.BUY, OrderKind.MARKET, D(1)), "unsupported_symbol"),
        (OrderRequest("BTC/USDT", Side.BUY, OrderKind.MARKET, D(1), reduce_only=True),
         "unsupported_reduce_only"),
        (OrderRequest("BTC/USDT", Side.BUY, OrderKind.LIMIT, D(1), price=D(101),
                      post_only=True), "post_only_would_take"),
        (OrderRequest("BTC/USDT", Side.BUY, OrderKind.MARKET, D(1), time_in_force="GTC"),
         "unsupported_time_in_force"),
    ],
)
def test_unsupported_order_features_reject_explicitly(order_request, reason):
    result = make_transport().submit(order_request)
    assert not result.accepted
    assert result.reason == reason
    assert result.order.state is OrderState.REJECTED


def test_paper_and_sandbox_instances_are_isolated_and_never_live_transports():
    paper = make_transport(mode=SimulationMode.PAPER)
    sandbox = make_transport(mode=SimulationMode.SANDBOX)
    paper.submit(market_buy())

    assert paper.balance_snapshot() != sandbox.balance_snapshot()
    assert not hasattr(paper, "create_order")
    assert not hasattr(paper, "connect")
    assert not hasattr(paper, "credentials")
    with pytest.raises(ValueError, match="live mode is unsupported"):
        make_transport(mode=SimulationMode.LIVE)
    with pytest.raises(TypeError):
        PaperTransport(
            market(), book(), {"BTC": D(1), "USDT": D(100)},
            fees=FeeModel(D(0), D(0)), credentials={"key": "not accepted"},
        )


def test_inactive_or_unsupported_markets_and_invalid_books_fail_closed():
    with pytest.raises(ValueError, match="explicitly active"):
        PaperTransport(
            market(active=None), book(), {"BTC": D(1), "USDT": D(100)},
            fees=FeeModel(D(0), D(0)),
        )
    with pytest.raises(ValueError, match="spot markets"):
        PaperTransport(
            market(market_type=MarketType.PERPETUAL), book(),
            {"BTC": D(1), "USDT": D(100)}, fees=FeeModel(D(0), D(0)),
        )
    with pytest.raises(ValueError, match="strictly price sorted"):
        PaperTransport(
            market(),
            OrderBook("BTC/USDT", (DepthLevel(D(98), D(1)), DepthLevel(D(99), D(1))),
                      (DepthLevel(D(101), D(1)),)),
            {"BTC": D(1), "USDT": D(100)}, fees=FeeModel(D(0), D(0)),
        )
