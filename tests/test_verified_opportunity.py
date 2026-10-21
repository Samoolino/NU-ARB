from arbx.hybrid.verified_opportunity import Opportunity, VenueSnapshot, evaluate_opportunity


def venue(name="a", **changes):
    values = dict(verified=True, balance_usd=12.0, book_age_ms=20.0, latency_ms=15.0,
                  permission_ok=True, stream_ok=True)
    values.update(changes)
    return VenueSnapshot(name, **values)


def opportunity(**changes):
    values = dict(expected_gross_usd=1.0, expected_fees_usd=0.1, expected_slippage_usd=0.1,
                  expected_network_usd=0.05, worst_case_reserve_usd=0.2,
                  required_capital_usd=8.0)
    values.update(changes)
    return Opportunity(**values)


def check(o, vs, **overrides):
    args = dict(now_book_age_limit_ms=100.0, latency_limit_ms=50.0,
                min_expected_net_usd=0.1, min_worst_case_net_usd=0.2)
    args.update(overrides)
    return evaluate_opportunity(o, vs, **args)


def test_verified_profit_and_available_capital_allow_engagement():
    result = check(opportunity(), (venue(),))
    assert result.ok
    assert result.reason == "verified_profitable_opportunity"
    assert result.expected_net_usd == 0.75
    assert result.worst_case_net_usd == 0.55
    assert result.deployable_capital_usd == 12.0


def test_session_capital_is_applied_without_starter_capital_assumption():
    result = check(opportunity(required_capital_usd=5.0), (venue(balance_usd=20.0),),
                  session_capital_usd=6.0)
    assert result.ok
    assert result.deployable_capital_usd == 6.0


def test_unverified_exchange_never_engages():
    result = check(opportunity(), (venue(verified=False),))
    assert not result.ok
    assert result.reason == "venue_unverified:a"


def test_stale_book_and_high_latency_are_rejected():
    assert check(opportunity(), (venue(book_age_ms=101.0),)).reason == "stale_book:a"
    assert check(opportunity(), (venue(latency_ms=51.0),)).reason == "latency_degraded:a"


def test_unprofitable_worst_case_and_profit_target_are_rejected():
    bad = opportunity(expected_gross_usd=0.1, expected_fees_usd=0.08, expected_slippage_usd=0.05)
    assert check(bad, (venue(),)).reason == "expected_net_below_floor"
    reached = opportunity(session_realized_pnl_usd=2.0, target_profit_usd=2.0)
    assert check(reached, (venue(),)).reason == "profit_target_attained"


def test_insufficient_funds_are_rejected():
    result = check(opportunity(required_capital_usd=13.0), (venue(balance_usd=12.0),))
    assert result.reason == "insufficient_verified_capital"


def test_nan_profit_is_rejected_fail_closed():
    result = check(opportunity(expected_gross_usd=float("nan")), (venue(),))
    assert not result.ok and result.reason == "invalid_numeric_input"


def test_infinite_balance_and_latency_are_rejected():
    assert check(opportunity(), (venue(balance_usd=float("inf")),)).reason == "invalid_numeric_snapshot:a"
    assert check(opportunity(), (venue(latency_ms=float("inf")),)).reason == "invalid_numeric_snapshot:a"
