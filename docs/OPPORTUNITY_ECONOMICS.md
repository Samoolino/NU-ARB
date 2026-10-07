# Deterministic opportunity economics (Phase 7)

`arbx.hybrid.opportunity_economics` is a pure calculator. It consumes explicit
scenario inputs and returns deterministic expected/worst-case economics,
probability of a positive net outcome, quote-equivalent liquidity, and a
fail-closed eligibility decision. It does not connect to venues, alter scanner or
execution behavior, submit orders, or guarantee an outcome.

## Model

Construct an `OpportunityEconomicsInput` with:

* A strictly positive `notional_usd`.
* A non-empty tuple of uniquely named `EconomicsScenario` values. Each has an
  explicit probability greater than zero; scenario probabilities must sum
  **exactly** to one.
* An explicit `EconomicsThresholds` record for expected/worst PnL, expected/worst
  basis points, and minimum probability positive.
* Explicit `costs_evidence_verified` and `liquidity_evidence_verified` values.
  Only `True` passes these evidence gates; `False` and `None` do not.
* Explicit output `rounding_decimals` (0–12). Outputs use decimal
  `ROUND_HALF_UP`; all eligibility comparisons use the unrounded values.

Every scenario supplies gross spread and the following separate costs in bps of
notional: buy fee, sell fee, slippage, market impact, funding, borrow,
withdrawal/network, FX, latency risk, partial-fill risk, rebalancing, capital
opportunity cost, and safety reserve. The calculation is:

```text
scenario_net_bps = gross_spread_bps - sum(all listed cost bps)
scenario_net_pnl = notional_usd * scenario_net_bps / 10,000
expected_net_pnl = probability-weighted scenario_net_pnl
worst_case_net_pnl = minimum scenario_net_pnl
probability_positive = sum(probabilities where scenario_net_pnl > 0)
```

Expected/worst bps use the same weighted/minimum operations on scenario net bps.
`liquidity_required` is the maximum scenario requirement; `liquidity_available`
is the minimum scenario availability. Callers must provide comparable USD
quote-equivalent figures that represent the constrained leg, not sum both legs
and double-count the same capital.

## Missing assumptions and evidence

Each spread, cost, and liquidity amount must be a finite number or explicitly
`None`. Negative costs and liquidity are invalid; gross spread and signed PnL
thresholds may be negative. `None` is never converted to zero. If any scenario
has an unknown gross spread or economic cost, all PnL/bps/probability outputs
are `None`. Unknown liquidity makes the corresponding aggregate `None`.
Unknown field names are returned in `unknown_assumptions` and are also eligibility
reasons. A fully numeric input still does not prove its assumptions: the
calculator cannot authenticate evidence. Unless the caller explicitly supplies
both evidence flags as `True`, the result is not eligible. That attestation must
come from the integrating system's real, current evidence process.

Eligibility additionally requires adequate availability in every scenario and
every configured minimum threshold to pass. The result is an analysis decision,
not an execution permit; no current strategy or execution path consumes this
module. Inputs are assumptions and scenarios, not verified forecasts or a
guarantee of realized returns.

The module contains no random sampling or hidden defaults. Repeating the same
input produces the same result.
