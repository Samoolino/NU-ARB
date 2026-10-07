import pathlib
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.capital_allocator import CapitalAllocationInput, allocate_capital


def inputs(**overrides):
    values = {
        "realized_eligible_profit_usd": "0.00",
        "available_equity_usd": "100.00",
        "reserved_capital_usd": "0.00",
        "unrealized_exposure_usd": "0.00",
        "maximum_position_usd": "25.00",
        "maximum_concurrent_positions": 3,
        "current_open_positions": 0,
        "venue_balance_usd": "100.00",
        "route_liquidity_usd": "100.00",
    }
    values.update(overrides)
    return CapitalAllocationInput(**values)


class CapitalAllocatorTests(unittest.TestCase):
    def test_compounds_only_explicit_realized_eligible_profit(self):
        no_realized_profit = allocate_capital(inputs())
        realized_profit = allocate_capital(inputs(realized_eligible_profit_usd="0.37"))
        self.assertEqual(no_realized_profit.deployable_capital_usd, Decimal("3.00"))
        self.assertEqual(realized_profit.deployable_capital_usd, Decimal("3.37"))
        self.assertEqual(no_realized_profit.capital_gate_evidence["profit_basis"],
                         "explicit_realized_eligible_profit_only")
        self.assertNotIn("unrealized_profit_usd", no_realized_profit.capital_gate_evidence)

    def test_reserves_and_unrealized_exposure_reduce_available_equity(self):
        result = allocate_capital(inputs(
            realized_eligible_profit_usd="10",
            available_equity_usd="10",
            reserved_capital_usd="2.01",
            unrealized_exposure_usd="1.01",
        ))
        self.assertEqual(result.deployable_capital_usd, Decimal("6.98"))
        self.assertEqual(
            result.capital_gate_evidence["equity_after_reserves_and_exposure_usd"],
            Decimal("6.98"),
        )
        self.assertEqual(
            result.limiting_constraints,
            ("available_equity_after_reserves_and_exposure",),
        )

    def test_position_cap_limits_capacity(self):
        result = allocate_capital(inputs(
            realized_eligible_profit_usd="5",
            maximum_position_usd="2.25",
        ))
        self.assertEqual(result.deployable_capital_usd, Decimal("2.25"))
        self.assertIn("maximum_position_usd", result.limiting_constraints)

    def test_maximum_concurrent_positions_denies_capacity(self):
        result = allocate_capital(inputs(
            maximum_concurrent_positions=2,
            current_open_positions=2,
        ))
        self.assertEqual(result.status, "denied")
        self.assertEqual(result.reason, "maximum_concurrent_positions_reached")
        self.assertEqual(result.deployable_capital_usd, Decimal("0.00"))
        self.assertFalse(result.capital_gate_evidence["checks"]["concurrent_position_slot_available"])

    def test_venue_balance_caps_capacity(self):
        result = allocate_capital(inputs(
            realized_eligible_profit_usd="5",
            venue_balance_usd="1.25",
        ))
        self.assertEqual(result.deployable_capital_usd, Decimal("1.25"))
        self.assertIn("venue_balance_usd", result.limiting_constraints)

    def test_route_liquidity_caps_capacity(self):
        result = allocate_capital(inputs(
            realized_eligible_profit_usd="5",
            route_liquidity_usd="1.10",
        ))
        self.assertEqual(result.deployable_capital_usd, Decimal("1.10"))
        self.assertIn("route_liquidity_usd", result.limiting_constraints)

    def test_money_values_round_conservatively_to_fixed_cents(self):
        result = allocate_capital(inputs(
            realized_eligible_profit_usd="0.019",
            available_equity_usd="10.009",
            reserved_capital_usd="0.011",
        ))
        self.assertEqual(result.deployable_capital_usd, Decimal("3.01"))
        self.assertEqual(
            result.capital_gate_evidence["reserved_capital_usd"], Decimal("0.02")
        )

    def test_malformed_missing_negative_and_nonfinite_inputs_fail_closed(self):
        invalid_cases = (
            ("realized_eligible_profit_usd", None),
            ("realized_eligible_profit_usd", "-0.01"),
            ("available_equity_usd", "-0.01"),
            ("reserved_capital_usd", float("nan")),
            ("unrealized_exposure_usd", float("inf")),
            ("maximum_position_usd", "not-a-number"),
            ("venue_balance_usd", Decimal("-1")),
            ("route_liquidity_usd", True),
        )
        for field, bad_value in invalid_cases:
            with self.subTest(field=field, value=bad_value):
                result = allocate_capital(inputs(**{field: bad_value}))
                self.assertEqual(result.status, "denied")
                self.assertEqual(result.reason, f"invalid_input:{field}")
                self.assertEqual(result.deployable_capital_usd, Decimal("0.00"))
                self.assertFalse(result.capital_gate_evidence["inputs_valid"])
                self.assertFalse(result.execution_permit)

    def test_malformed_position_counts_fail_closed(self):
        for field, bad_value in (
            ("maximum_concurrent_positions", -1),
            ("current_open_positions", 1.5),
            ("current_open_positions", True),
        ):
            with self.subTest(field=field, value=bad_value):
                result = allocate_capital(inputs(**{field: bad_value}))
                self.assertEqual(result.status, "denied")
                self.assertEqual(result.reason, f"invalid_input:{field}")
                self.assertEqual(result.deployable_capital_usd, Decimal("0.00"))

    def test_insufficient_equity_and_zero_route_liquidity_deny_capacity(self):
        reserve_denial = allocate_capital(inputs(
            available_equity_usd="3",
            reserved_capital_usd="2",
            unrealized_exposure_usd="1",
        ))
        route_denial = allocate_capital(inputs(route_liquidity_usd="0"))
        self.assertEqual(reserve_denial.status, "denied")
        self.assertEqual(reserve_denial.reason, "reserved_and_unrealized_exposure_exhaust_equity")
        self.assertEqual(route_denial.status, "denied")
        self.assertEqual(route_denial.reason, "route_liquidity_unavailable")

    def test_deterministic_capacity_does_not_initiate_trade_or_grant_permit(self):
        request = inputs(realized_eligible_profit_usd="0.40")
        first = allocate_capital(request)
        second = allocate_capital(request)
        self.assertEqual(first, second)
        self.assertEqual(first.deployable_capital_usd, Decimal("3.40"))
        self.assertFalse(first.trade_initiated)
        self.assertFalse(first.execution_permit)
        self.assertEqual(
            first.reason,
            "capital_capacity_available_not_execution_authorization",
        )


if __name__ == "__main__":
    unittest.main()
