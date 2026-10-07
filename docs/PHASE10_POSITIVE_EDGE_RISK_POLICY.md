# Phase 10 — Positive-edge risk policy and circuit breaker

This phase implements a fail-closed policy around modeled positive-edge inputs.
It does **not** promise profit or no loss, and it does not claim a loss is
impossible. Modeled expected and worst-case PnL are estimates, not guarantees;
execution, fees, partial fills, outages, and reconciliation may produce losses.
The policy does not place orders or grant an execution permit.

## Pretrade inputs

`PositiveEdgeRiskPolicy.pretrade(...)` accepts expected PnL, worst-case PnL, and
separate minimum expected/worst-case PnL thresholds. Both modeled PnL estimates
and both thresholds must be finite and positive, and each estimate must meet
its own threshold. These values are pretrade decision inputs only. They do not
assert that an order will fill at those values or that the realized result will
be positive.

## Breaker stages

The deterministic stages are:

1. `active`: policy checks may allow additional trading subject to every other
   existing authorization and risk check.
2. `reconciliation_required`: any negative realized PnL, unknown PnL, failed or
   ambiguous live outcome, or reported account/order uncertainty denies new
   trading. The policy records a sanitized incident code and typed facts.
3. `awaiting_operator_restart`: reached only when reconciliation explicitly
   supplies finite realized PnL, matching balances, no open or unknown orders,
   and no partial or unknown fills. Trading remains denied.
4. `active`: reached only by calling `restart(operator_confirmed=True)` after
   successful reconciliation. A false/missing confirmation, later loss, or
   later uncertainty cannot clear the halt.

Zero realized PnL alone does not trigger the loss breaker. It does not clear a
previous halt either. Unknown PnL, missing reconciliation, balance mismatch,
open/unknown orders, and partial/unknown fills fail closed. The module does not
perform exchange reconciliation; a trusted operator-facing integration must
obtain current account/order evidence before invoking the reconciliation API.
The restart method itself is an explicit API confirmation, not an operator
identity or authorization system; no restart UI is implemented in this phase.

## Persistence and integration

`PositiveEdgeRiskPolicy` is pure with respect to exchange I/O and accepts an
optional `RiskPolicyStore`. `TradeJournal` implements that interface in SQLite:
the global breaker state survives process restarts, and incident rows persist
only an allow-listed code, finite realized PnL when known, boolean/unknown
balance/order/fill facts, and policy stage. No exception text, credentials,
order IDs, or arbitrary caller strings are stored as incident facts.

This phase is not wired into the scanner, `Hub`, or `ExecutionGate`; it does not
change live order behavior. A caller must explicitly supply outcome and
reconciliation facts to the policy. The module itself performs no exchange
requests, order cancellation, unwind, or automatic restart. Callers that use
the durable interface must preserve the returned deny state in their own
authorization path; this module does not grant order permits.

Focused offline verification:

```powershell
python -m pytest -q tests/test_phase10_risk_policy.py tests/test_journal_opportunities.py
python -m py_compile arb_bot/arbx/risk_policy.py arb_bot/arbx/journal.py
```

This is a local risk-control stage, not evidence that a venue was connected,
that an order was submitted, or that a no-loss/profit outcome is achievable.
