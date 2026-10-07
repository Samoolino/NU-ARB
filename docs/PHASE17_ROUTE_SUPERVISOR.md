# Phase 17 — Deterministic route fault supervisor

`arbx.route_supervisor.RouteSupervisor` is an isolated, deterministic,
in-memory state model. It enumerates route faults for WebSocket disconnect,
REST timeout, clock drift, balance mismatch, unknown order, partial fill,
duplicate intent/order, stale book, unexpected fee, venue outage,
authentication/permission failure, nonce/rate-limit, and network mismatch.

A fault creates a sanitized incident with a fixed reason code, blocks new
attempts on that route, and requires reconciliation. Other registered routes
remain unaffected. `assert_attempt_allowed` raises a fixed safe error for any
blocked route. Reconciliation evidence advances the route only to
`operator_reset_required`; it does not unblock attempts. A separately invoked
operator reset with an authorized assertion and a safe operator identifier is
required before the route returns to `ready`. A later fault clears prior
reconciliation evidence. The model has no retry, transport, logging, order,
or exchange-response interfaces and does not accept exception details.

## Phase 16 lifecycle boundary

The model accepts recognized Phase 16 `OrderLifecycleState` identifiers only
from its explicit safe-state allow-list. `submitting`, `timed_out`, `unknown`,
and unrecognized values are rejected as ambiguous. Lifecycle state identifiers
are optional annotations/evidence, not proof of reconciliation; a route remains
blocked until typed reconciliation evidence and operator reset are both
recorded. Phase 16 itself remains unchanged.

## Scope and integration limitations

This phase is intentionally **not wired into live or paper execution**. State
is process-local and in-memory, not durable across restart. The
`operator_authorized` argument is a caller assertion, not authentication or
authorization enforcement. No scanner/transport call site is gated by this
model, and no venue connectivity or execution capability is added. An
integrator must provide durable state, authenticated operator authorization,
fault-to-route attribution, category-appropriate reconciliation procedures,
and fail-closed execution gating before relying on it operationally.

Focused offline validation:

```powershell
$env:PYTHONPATH = "arb_bot"
python -m py_compile arb_bot\arbx\route_supervisor.py tests\test_route_supervisor.py
python -m pytest -q tests\test_route_supervisor.py
```
