"""Pure deterministic opportunity-economics model.

The calculator evaluates explicitly supplied scenarios. It does not fetch market
data, estimate missing costs, simulate random outcomes, or authorize execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import TypeAlias

Number: TypeAlias = int | float | Decimal
OptionalNumber: TypeAlias = Number | None

_COST_FIELDS = (
    "buy_fee_bps",
    "sell_fee_bps",
    "slippage_bps",
    "market_impact_bps",
    "funding_bps",
    "borrow_bps",
    "withdrawal_network_bps",
    "fx_bps",
    "latency_risk_bps",
    "partial_fill_risk_bps",
    "rebalancing_bps",
    "capital_opportunity_cost_bps",
    "safety_reserve_bps",
)
_LIQUIDITY_FIELDS = ("liquidity_required_usd", "liquidity_available_usd")
_SCENARIO_FIELDS = ("gross_spread_bps", *_COST_FIELDS, *_LIQUIDITY_FIELDS)


@dataclass(frozen=True, slots=True)
class EconomicsScenario:
    """One possible outcome, with its explicit probability and assumptions.

    Cost and gross-spread inputs are basis points of ``notional_usd``. ``None``
    means unknown, never zero. Liquidity is quote-equivalent USD, with available
    liquidity representing the constrained leg's concurrently usable amount.
    """

    name: str
    probability: Number
    gross_spread_bps: OptionalNumber
    buy_fee_bps: OptionalNumber
    sell_fee_bps: OptionalNumber
    slippage_bps: OptionalNumber
    market_impact_bps: OptionalNumber
    funding_bps: OptionalNumber
    borrow_bps: OptionalNumber
    withdrawal_network_bps: OptionalNumber
    fx_bps: OptionalNumber
    latency_risk_bps: OptionalNumber
    partial_fill_risk_bps: OptionalNumber
    rebalancing_bps: OptionalNumber
    capital_opportunity_cost_bps: OptionalNumber
    safety_reserve_bps: OptionalNumber
    liquidity_required_usd: OptionalNumber
    liquidity_available_usd: OptionalNumber


@dataclass(frozen=True, slots=True)
class EconomicsThresholds:
    """Explicit minimums; eligibility comparisons use unrounded values."""

    minimum_expected_net_pnl_usd: Number
    minimum_worst_case_net_pnl_usd: Number
    minimum_expected_bps: Number
    minimum_worst_case_bps: Number
    minimum_probability_positive: Number


@dataclass(frozen=True, slots=True)
class OpportunityEconomicsInput:
    """Complete caller-supplied model inputs; no economic defaults are provided."""

    notional_usd: Number
    scenarios: tuple[EconomicsScenario, ...]
    thresholds: EconomicsThresholds
    costs_evidence_verified: bool | None
    liquidity_evidence_verified: bool | None
    rounding_decimals: int


@dataclass(frozen=True, slots=True)
class ScenarioEconomics:
    name: str
    probability: Decimal
    gross_spread_bps: Decimal | None
    total_cost_bps: Decimal | None
    net_bps: Decimal | None
    net_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class OpportunityEconomicsResult:
    expected_net_pnl: Decimal | None
    worst_case_net_pnl: Decimal | None
    expected_bps: Decimal | None
    worst_case_bps: Decimal | None
    probability_positive: Decimal | None
    liquidity_required: Decimal | None
    liquidity_available: Decimal | None
    executable_eligible: bool
    eligibility_reasons: tuple[str, ...]
    unknown_assumptions: tuple[str, ...]
    scenario_results: tuple[ScenarioEconomics, ...]


def _decimal(value: object, field: str, *, allow_none: bool = False) -> Decimal | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{field}: expected a finite number" + (" or None" if allow_none else ""))
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field}: expected a finite number") from None
    if not number.is_finite():
        raise ValueError(f"{field}: expected a finite number")
    return number


def _rounded(value: Decimal | None, places: int) -> Decimal | None:
    if value is None:
        return None
    quantum = Decimal(1).scaleb(-places)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def calculate_opportunity_economics(
    inputs: OpportunityEconomicsInput,
) -> OpportunityEconomicsResult:
    """Calculate weighted expected and worst-case returns without randomness.

    Every scenario contributes to the expected value using its declared
    probability. The worst case is the lowest scenario net return (including
    positive-probability scenarios only; zero-probability scenarios are rejected).
    Unknown gross spread or cost makes all PnL/probability outputs unavailable;
    unknown liquidity makes the relevant liquidity aggregate unavailable.
    """

    if not isinstance(inputs, OpportunityEconomicsInput):
        raise ValueError("inputs: expected OpportunityEconomicsInput")

    notional = _decimal(inputs.notional_usd, "notional_usd")
    assert notional is not None
    if notional <= 0:
        raise ValueError("notional_usd: must be greater than zero")

    if not isinstance(inputs.scenarios, tuple) or not inputs.scenarios:
        raise ValueError("scenarios: expected a non-empty tuple")
    if not isinstance(inputs.thresholds, EconomicsThresholds):
        raise ValueError("thresholds: expected EconomicsThresholds")
    if isinstance(inputs.rounding_decimals, bool) or not isinstance(inputs.rounding_decimals, int):
        raise ValueError("rounding_decimals: expected an integer from 0 to 12")
    if not 0 <= inputs.rounding_decimals <= 12:
        raise ValueError("rounding_decimals: expected an integer from 0 to 12")
    if (
        inputs.costs_evidence_verified is not True
        and inputs.costs_evidence_verified is not False
        and inputs.costs_evidence_verified is not None
    ):
        raise ValueError("costs_evidence_verified: expected True, False, or None")
    if (
        inputs.liquidity_evidence_verified is not True
        and inputs.liquidity_evidence_verified is not False
        and inputs.liquidity_evidence_verified is not None
    ):
        raise ValueError("liquidity_evidence_verified: expected True, False, or None")

    places = inputs.rounding_decimals
    threshold_values = {
        field: _decimal(getattr(inputs.thresholds, field), f"thresholds.{field}")
        for field in (
            "minimum_expected_net_pnl_usd",
            "minimum_worst_case_net_pnl_usd",
            "minimum_expected_bps",
            "minimum_worst_case_bps",
            "minimum_probability_positive",
        )
    }
    min_probability = threshold_values["minimum_probability_positive"]
    assert min_probability is not None
    if not Decimal(0) <= min_probability <= Decimal(1):
        raise ValueError("thresholds.minimum_probability_positive: must be between zero and one")

    normalized: list[dict[str, Decimal | None]] = []
    names: set[str] = set()
    for index, scenario in enumerate(inputs.scenarios):
        prefix = f"scenarios[{index}]"
        if not isinstance(scenario, EconomicsScenario):
            raise ValueError(f"{prefix}: expected EconomicsScenario")
        if not isinstance(scenario.name, str) or not scenario.name.strip():
            raise ValueError(f"{prefix}.name: must be a non-empty string")
        if scenario.name in names:
            raise ValueError(f"{prefix}.name: scenario names must be unique")
        names.add(scenario.name)

        values: dict[str, Decimal | None] = {}
        probability = _decimal(scenario.probability, f"{prefix}.probability")
        assert probability is not None
        if not Decimal(0) < probability <= Decimal(1):
            raise ValueError(f"{prefix}.probability: must be greater than zero and at most one")
        values["probability"] = probability
        for field in _SCENARIO_FIELDS:
            value = _decimal(
                getattr(scenario, field),
                f"{prefix}.{field}",
                allow_none=True,
            )
            if value is not None and field in _COST_FIELDS and value < 0:
                raise ValueError(f"{prefix}.{field}: must be nonnegative")
            if value is not None and field in _LIQUIDITY_FIELDS and value < 0:
                raise ValueError(f"{prefix}.{field}: must be nonnegative")
            values[field] = value
        normalized.append(values)

    total_probability = sum((row["probability"] for row in normalized), Decimal(0))
    if total_probability != Decimal(1):
        raise ValueError("scenarios.probability: probabilities must sum exactly to one")

    unknown: list[str] = []
    scenario_results: list[ScenarioEconomics] = []
    raw_nets: list[tuple[Decimal, Decimal]] = []
    required_values: list[Decimal] = []
    available_values: list[Decimal] = []

    for scenario, values in zip(inputs.scenarios, normalized):
        missing_economics = [
            field
            for field in ("gross_spread_bps", *_COST_FIELDS)
            if values[field] is None
        ]
        unknown.extend(f"{scenario.name}.{field}" for field in missing_economics)
        gross = values["gross_spread_bps"]
        total_cost: Decimal | None = None
        net_bps: Decimal | None = None
        net_pnl: Decimal | None = None
        if not missing_economics:
            assert gross is not None
            total_cost = sum((values[field] for field in _COST_FIELDS), Decimal(0))
            net_bps = gross - total_cost
            net_pnl = notional * net_bps / Decimal(10_000)
            probability = values["probability"]
            assert probability is not None
            raw_nets.append((probability, net_bps))

        for field in _LIQUIDITY_FIELDS:
            if values[field] is None:
                unknown.append(f"{scenario.name}.{field}")
        required = values["liquidity_required_usd"]
        available = values["liquidity_available_usd"]
        if required is not None:
            required_values.append(required)
        if available is not None:
            available_values.append(available)

        scenario_results.append(
            ScenarioEconomics(
                scenario.name,
                values["probability"],  # type: ignore[arg-type]
                gross,
                total_cost,
                net_bps,
                net_pnl,
            )
        )

    economics_complete = all(
        row["gross_spread_bps"] is not None
        and all(row[field] is not None for field in _COST_FIELDS)
        for row in normalized
    )
    if economics_complete:
        expected_bps_raw = sum(
            (probability * net_bps for probability, net_bps in raw_nets),
            Decimal(0),
        )
        expected_pnl_raw = notional * expected_bps_raw / Decimal(10_000)
        worst_bps_raw = min(net_bps for _, net_bps in raw_nets)
        worst_pnl_raw = notional * worst_bps_raw / Decimal(10_000)
        probability_positive_raw = sum(
            (probability for probability, net_bps in raw_nets if net_bps > 0),
            Decimal(0),
        )
    else:
        expected_bps_raw = expected_pnl_raw = None
        worst_bps_raw = worst_pnl_raw = probability_positive_raw = None

    liquidity_required_raw = (
        max(required_values) if len(required_values) == len(normalized) else None
    )
    liquidity_available_raw = (
        min(available_values) if len(available_values) == len(normalized) else None
    )

    reasons: list[str] = []
    if unknown:
        reasons.extend(f"unknown_assumption:{item}" for item in unknown)
    if inputs.costs_evidence_verified is not True:
        reasons.append("cost_evidence_not_verified")
    if inputs.liquidity_evidence_verified is not True:
        reasons.append("liquidity_evidence_not_verified")
    if (
        liquidity_required_raw is not None
        and liquidity_available_raw is not None
        and liquidity_available_raw < liquidity_required_raw
    ):
        reasons.append("insufficient_liquidity")

    checks = (
        (expected_pnl_raw, threshold_values["minimum_expected_net_pnl_usd"],
         "minimum_expected_net_pnl_not_met"),
        (worst_pnl_raw, threshold_values["minimum_worst_case_net_pnl_usd"],
         "minimum_worst_case_net_pnl_not_met"),
        (expected_bps_raw, threshold_values["minimum_expected_bps"],
         "minimum_expected_bps_not_met"),
        (worst_bps_raw, threshold_values["minimum_worst_case_bps"],
         "minimum_worst_case_bps_not_met"),
        (probability_positive_raw, min_probability, "minimum_probability_positive_not_met"),
    )
    for actual, minimum, reason in checks:
        if actual is not None and minimum is not None and actual < minimum:
            reasons.append(reason)

    return OpportunityEconomicsResult(
        expected_net_pnl=_rounded(expected_pnl_raw, places),
        worst_case_net_pnl=_rounded(worst_pnl_raw, places),
        expected_bps=_rounded(expected_bps_raw, places),
        worst_case_bps=_rounded(worst_bps_raw, places),
        probability_positive=_rounded(probability_positive_raw, places),
        liquidity_required=_rounded(liquidity_required_raw, places),
        liquidity_available=_rounded(liquidity_available_raw, places),
        executable_eligible=not reasons,
        eligibility_reasons=tuple(reasons),
        unknown_assumptions=tuple(unknown),
        scenario_results=tuple(scenario_results),
    )
