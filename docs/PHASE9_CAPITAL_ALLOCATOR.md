# Phase 9 deterministic capital allocator

`arbx.capital_allocator` is a pure, offline capacity calculator. It is not
wired to scanner, venue, strategy, or order behavior. Its positive output is a
maximum capital ceiling only: it neither selects an opportunity nor initiates a
trade, and `execution_permit` is always false.

## Policy and formula

The fixed starter is exactly **$3.00**. The unbounded compounding target is:

```text
target = $3.00 + explicitly supplied eligible realized profit
usable equity = available equity - reserved capital - unrealized exposure
deployable capacity = min(
    target,
    max(0, usable equity),
    maximum position,
    venue balance,
    route liquidity
)
```

Available equity is interpreted as gross equity before the separately supplied
reservations and unrealized exposure. Venue balance and route liquidity are
independent USD-equivalent caps. The eligible-profit input is the only profit
component; opportunity estimates and unrealized gains are not accepted or
compounded. The caller remains responsible for supplying profit already
classified as eligible and realized.

Maximum concurrent positions is a hard gate: when open positions equal or
exceed the explicit maximum, capacity is denied. It does not increase capital
capacity when an opportunity appears or divide the capacity into a hidden
per-position fallback.

## Validation and result

Every money amount and both position counts are required. Missing, negative,
nonfinite, malformed, and incorrectly typed inputs fail closed with zero
capacity and an `invalid_input:<field>` reason. Money inputs use fixed-cent
rounding: equity, position, venue-balance, route-liquidity, and profit amounts
round down; reserves and unrealized exposure round up. This avoids overstating
usable funds. Position counts must be nonnegative integers.

`CapitalAllocationResult` returns the amount, status, explicit reason, limiting
constraints, and immutable gate evidence containing the normalized inputs,
equity after commitments, compounded target, and individual checks. Repeating
the same request returns the same result. No exchange request, database access,
market/opportunity input, order, or execution authorization is part of this
module.

Focused offline tests:

```powershell
python -m pytest -q tests/test_capital_allocator.py
python -m py_compile arb_bot/arbx/capital_allocator.py
```
