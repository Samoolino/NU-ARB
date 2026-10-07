"""Deterministic, non-executing Phase 9 capital-capacity calculation.

This pure module is intentionally not connected to a scanner, venue, or order
path. A positive result describes a capital ceiling only; it is not a trade
decision or execution authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP, localcontext
from types import MappingProxyType
from typing import Mapping

from arbx.config import LIVE_STARTER_CAPITAL_USD

STARTER_CAPITAL_USD = Decimal(str(LIVE_STARTER_CAPITAL_USD))
_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")
_MAX_DECIMAL_PRECISION = 64


@dataclass(frozen=True, slots=True)
class CapitalAllocationInput:
    """All capacity inputs are mandatory; ``None`` is never treated as zero."""

    realized_eligible_profit_usd: object
    available_equity_usd: object
    reserved_capital_usd: object
    unrealized_exposure_usd: object
    maximum_position_usd: object
    maximum_concurrent_positions: object
    current_open_positions: object
    venue_balance_usd: object
    route_liquidity_usd: object


@dataclass(frozen=True, slots=True)
class CapitalAllocationResult:
    """A capacity estimate, never an execution permit or an order instruction."""

    deployable_capital_usd: Decimal
    status: str
    reason: str
    limiting_constraints: tuple[str, ...]
    capital_gate_evidence: Mapping[str, object]
    execution_permit: bool = False
    trade_initiated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "capital_gate_evidence",
            MappingProxyType(dict(self.capital_gate_evidence)),
        )


_MONEY_FIELDS = (
    "realized_eligible_profit_usd",
    "available_equity_usd",
    "reserved_capital_usd",
    "unrealized_exposure_usd",
    "maximum_position_usd",
    "venue_balance_usd",
    "route_liquidity_usd",
)
_COUNT_FIELDS = ("maximum_concurrent_positions", "current_open_positions")


def _parse_money(value: object, field: str) -> tuple[Decimal | None, str | None]:
    if value is None or isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        return None, f"invalid_input:{field}"
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None, f"invalid_input:{field}"
    if not parsed.is_finite() or parsed < 0:
        return None, f"invalid_input:{field}"
    rounding = ROUND_UP if field in ("reserved_capital_usd", "unrealized_exposure_usd") else ROUND_DOWN
    try:
        with localcontext() as context:
            context.prec = _MAX_DECIMAL_PRECISION
            return parsed.quantize(_CENT, rounding=rounding), None
    except (InvalidOperation, ValueError):
        return None, f"invalid_input:{field}"


def _parse_count(value: object, field: str) -> tuple[int | None, str | None]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None, f"invalid_input:{field}"
    return value, None


def allocate_capital(inputs: CapitalAllocationInput) -> CapitalAllocationResult:
    """Return a fixed-cent deployable ceiling from explicit, fail-closed inputs.

    Available equity is treated as gross equity before subtracting both
    reservations and unrealized exposure. Venue balance and route liquidity are
    independently supplied quote-USD caps. Position count is a hard eligibility
    gate; it does not multiply or otherwise inflate capital capacity.
    """
    if not isinstance(inputs, CapitalAllocationInput):
        inputs = CapitalAllocationInput(
            realized_eligible_profit_usd=None,
            available_equity_usd=None,
            reserved_capital_usd=None,
            unrealized_exposure_usd=None,
            maximum_position_usd=None,
            maximum_concurrent_positions=None,
            current_open_positions=None,
            venue_balance_usd=None,
            route_liquidity_usd=None,
        )

    values: dict[str, Decimal | int | None] = {}
    errors: list[str] = []
    for field in _MONEY_FIELDS:
        parsed, error = _parse_money(getattr(inputs, field), field)
        values[field] = parsed
        if error:
            errors.append(error)
    for field in _COUNT_FIELDS:
        parsed_count, error = _parse_count(getattr(inputs, field), field)
        values[field] = parsed_count
        if error:
            errors.append(error)

    evidence: dict[str, object] = {
        "starter_capital_usd": STARTER_CAPITAL_USD,
        "profit_basis": "explicit_realized_eligible_profit_only",
        "execution_permit": False,
        "trade_initiated": False,
        "deployable_capital_usd": _ZERO,
        **values,
    }
    if errors:
        evidence.update({
            "inputs_valid": False,
            "validation_errors": tuple(errors),
            "equity_after_reserves_and_exposure_usd": None,
            "compounded_target_usd": None,
            "checks": MappingProxyType({}),
        })
        return CapitalAllocationResult(
            _ZERO, "denied", errors[0], (), evidence,
        )

    profit = values["realized_eligible_profit_usd"]
    equity = values["available_equity_usd"]
    reserved = values["reserved_capital_usd"]
    exposure = values["unrealized_exposure_usd"]
    max_position = values["maximum_position_usd"]
    max_positions = values["maximum_concurrent_positions"]
    open_positions = values["current_open_positions"]
    venue_balance = values["venue_balance_usd"]
    route_liquidity = values["route_liquidity_usd"]
    # The validation pass above guarantees these narrowed types.
    assert all(isinstance(value, Decimal) for value in (
        profit, equity, reserved, exposure, max_position, venue_balance, route_liquidity,
    ))
    assert isinstance(max_positions, int) and isinstance(open_positions, int)

    target = STARTER_CAPITAL_USD + profit
    equity_after_commitments = equity - reserved - exposure
    usable_equity = max(_ZERO, equity_after_commitments)
    checks = {
        "equity_after_reserves_and_exposure_positive": usable_equity > _ZERO,
        "reserved_and_unrealized_exposure_within_equity": equity_after_commitments >= _ZERO,
        "maximum_position_positive": max_position > _ZERO,
        "concurrent_position_slot_available": open_positions < max_positions,
        "venue_balance_positive": venue_balance > _ZERO,
        "route_liquidity_positive": route_liquidity > _ZERO,
        "realized_profit_only": True,
    }
    evidence.update({
        "inputs_valid": True,
        "compounded_target_usd": target,
        "equity_after_reserves_and_exposure_usd": equity_after_commitments,
        "checks": MappingProxyType(checks),
    })

    if open_positions >= max_positions:
        return CapitalAllocationResult(
            _ZERO, "denied", "maximum_concurrent_positions_reached", (), evidence,
        )
    if equity_after_commitments <= _ZERO:
        reason = (
            "reserved_and_unrealized_exposure_exhaust_equity"
            if reserved + exposure >= equity else "no_uncommitted_equity"
        )
        return CapitalAllocationResult(_ZERO, "denied", reason, (), evidence)
    for field, value, reason in (
        ("maximum_position_usd", max_position, "no_position_capacity"),
        ("venue_balance_usd", venue_balance, "venue_balance_unavailable"),
        ("route_liquidity_usd", route_liquidity, "route_liquidity_unavailable"),
    ):
        if value <= _ZERO:
            return CapitalAllocationResult(_ZERO, "denied", reason, (field,), evidence)

    limits = (
        ("compounded_starter_and_realized_profit", target),
        ("available_equity_after_reserves_and_exposure", usable_equity),
        ("maximum_position_usd", max_position),
        ("venue_balance_usd", venue_balance),
        ("route_liquidity_usd", route_liquidity),
    )
    capacity = min(amount for _, amount in limits)
    limiting = tuple(name for name, amount in limits if amount == capacity)
    if capacity <= _ZERO:
        return CapitalAllocationResult(_ZERO, "denied", "no_deployable_capacity", limiting, evidence)
    evidence["deployable_capital_usd"] = capacity
    return CapitalAllocationResult(
        capacity,
        "available",
        "capital_capacity_available_not_execution_authorization",
        limiting,
        evidence,
    )
