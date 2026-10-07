from __future__ import annotations

import pathlib
import sys
from dataclasses import replace
from decimal import Decimal

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx.hybrid.opportunity_economics import (
    EconomicsScenario,
    EconomicsThresholds,
    OpportunityEconomicsInput,
    calculate_opportunity_economics,
)


def scenario(name: str, probability: float, spread: float = 100.0, **updates):
    values = dict(
        name=name,
        probability=probability,
        gross_spread_bps=spread,
        buy_fee_bps=10,
        sell_fee_bps=10,
        slippage_bps=5,
        market_impact_bps=5,
        funding_bps=1,
        borrow_bps=2,
        withdrawal_network_bps=3,
        fx_bps=4,
        latency_risk_bps=5,
        partial_fill_risk_bps=6,
        rebalancing_bps=7,
        capital_opportunity_cost_bps=8,
        safety_reserve_bps=9,
        liquidity_required_usd=50,
        liquidity_available_usd=75,
    )
    values.update(updates)
    return EconomicsScenario(**values)


def inputs(*scenarios, **updates):
    values = dict(
        notional_usd=1000,
        scenarios=tuple(scenarios),
        thresholds=EconomicsThresholds(
            minimum_expected_net_pnl_usd=0,
            minimum_worst_case_net_pnl_usd=0,
            minimum_expected_bps=0,
            minimum_worst_case_bps=0,
            minimum_probability_positive=0.5,
        ),
        costs_evidence_verified=True,
        liquidity_evidence_verified=True,
        rounding_decimals=4,
    )
    values.update(updates)
    return OpportunityEconomicsInput(**values)


def test_accounts_for_every_cost_and_computes_weighted_expected_and_worst_case():
    favorable = scenario("favorable", 0.75, spread=100)
    adverse = scenario("adverse", 0.25, spread=50)
    result = calculate_opportunity_economics(inputs(favorable, adverse))

    # Sum of all 13 explicitly modeled costs is 75 bps.
    assert result.scenario_results[0].total_cost_bps == Decimal(75)
    assert result.scenario_results[0].net_bps == Decimal(25)
    assert result.expected_bps == Decimal("12.5000")
    assert result.worst_case_bps == Decimal("-25.0000")
    assert result.expected_net_pnl == Decimal("1.2500")
    assert result.worst_case_net_pnl == Decimal("-2.5000")
    assert result.probability_positive == Decimal("0.7500")


def test_unknown_cost_is_not_zero_and_fails_closed():
    unknown = scenario("base", 1, buy_fee_bps=None)
    result = calculate_opportunity_economics(inputs(unknown))

    assert result.expected_net_pnl is None
    assert result.worst_case_net_pnl is None
    assert result.expected_bps is None
    assert result.probability_positive is None
    assert result.unknown_assumptions == ("base.buy_fee_bps",)
    assert not result.executable_eligible
    assert "unknown_assumption:base.buy_fee_bps" in result.eligibility_reasons


def test_unknown_or_unverified_evidence_fails_closed():
    result = calculate_opportunity_economics(
        inputs(
            scenario("base", 1),
            costs_evidence_verified=None,
            liquidity_evidence_verified=False,
        )
    )
    assert result.expected_net_pnl == Decimal("2.5000")
    assert not result.executable_eligible
    assert result.eligibility_reasons == (
        "cost_evidence_not_verified",
        "liquidity_evidence_not_verified",
    )


def test_thresholds_compare_unrounded_values_and_outputs_round_half_up():
    values = inputs(
        scenario("base", 1, spread=75.005),
        thresholds=EconomicsThresholds(
            minimum_expected_net_pnl_usd=Decimal("0.0049"),
            minimum_worst_case_net_pnl_usd=Decimal("0.0049"),
            minimum_expected_bps=Decimal("0.0049"),
            minimum_worst_case_bps=Decimal("0.0049"),
            minimum_probability_positive=1,
        ),
        notional_usd=10000,
        rounding_decimals=2,
    )
    result = calculate_opportunity_economics(values)
    assert result.expected_net_pnl == Decimal("0.01")
    assert result.expected_bps == Decimal("0.01")
    assert result.executable_eligible

    fails = calculate_opportunity_economics(
        replace(
            values,
            thresholds=replace(
                values.thresholds,
                minimum_expected_bps=Decimal("0.0051"),
            ),
        )
    )
    assert "minimum_expected_bps_not_met" in fails.eligibility_reasons
    assert not fails.executable_eligible


def test_liquidity_uses_conservative_scenario_bounds_and_fails_when_short():
    result = calculate_opportunity_economics(
        inputs(
            scenario("a", 0.5, liquidity_required_usd=40, liquidity_available_usd=120),
            scenario("b", 0.5, liquidity_required_usd=80, liquidity_available_usd=60),
        )
    )
    assert result.liquidity_required == Decimal("80.0000")
    assert result.liquidity_available == Decimal("60.0000")
    assert "insufficient_liquidity" in result.eligibility_reasons
    assert not result.executable_eligible


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"notional_usd": 0}, "notional_usd"),
        ({"notional_usd": float("nan")}, "finite number"),
        ({"scenarios": ()}, "non-empty tuple"),
        ({"rounding_decimals": 13}, "rounding_decimals"),
        ({"costs_evidence_verified": "yes"}, "costs_evidence_verified"),
    ],
)
def test_malformed_top_level_inputs_are_rejected(change, message):
    base = inputs(scenario("base", 1))
    with pytest.raises(ValueError, match=message):
        calculate_opportunity_economics(replace(base, **change))


def test_evidence_flags_reject_truthy_non_booleans():
    with pytest.raises(ValueError, match="costs_evidence_verified"):
        calculate_opportunity_economics(
            inputs(scenario("base", 1), costs_evidence_verified=1)
        )


@pytest.mark.parametrize(
    ("scenario_changes", "message"),
    [
        ({"buy_fee_bps": -1}, "buy_fee_bps"),
        ({"gross_spread_bps": float("inf")}, "finite number"),
        ({"liquidity_available_usd": -0.01}, "liquidity_available_usd"),
        ({"probability": 0}, "probability"),
        ({"probability": True}, "finite number"),
    ],
)
def test_malformed_scenario_assumptions_are_rejected(scenario_changes, message):
    base = inputs(scenario("base", 1))
    changed = replace(base, scenarios=(replace(base.scenarios[0], **scenario_changes),))
    with pytest.raises(ValueError, match=message):
        calculate_opportunity_economics(changed)


def test_probabilities_must_sum_exactly_to_one_and_scenario_names_be_unique():
    base = inputs(scenario("a", 0.4), scenario("b", 0.5))
    with pytest.raises(ValueError, match="sum exactly to one"):
        calculate_opportunity_economics(base)
    duplicate = inputs(scenario("same", 0.5), scenario("same", 0.5))
    with pytest.raises(ValueError, match="unique"):
        calculate_opportunity_economics(duplicate)


def test_unknown_liquidity_remains_explicit_and_blocks_eligibility():
    result = calculate_opportunity_economics(
        inputs(scenario("base", 1, liquidity_available_usd=None))
    )
    assert result.liquidity_available is None
    assert "base.liquidity_available_usd" in result.unknown_assumptions
    assert not result.executable_eligible


def test_repeated_calculation_is_deterministic():
    values = inputs(scenario("positive", 0.6), scenario("negative", 0.4, spread=60))
    assert calculate_opportunity_economics(values) == calculate_opportunity_economics(values)
