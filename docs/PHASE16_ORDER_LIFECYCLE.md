# Phase 16 — Typed order lifecycle intent and durable state

`arbx.order_lifecycle` records an order's local intent and evidence-based
lifecycle state. It is deliberately separate from venue adapters and order
transport: this module exposes no exchange submit or cancel methods and sends
no network requests.

Each persisted order contains its intent ID, client order ID, optional venue
order ID, route and opportunity IDs, creation timestamp, strategy, allocation,
quantity, expected and realized PnL, fill quantity, lifecycle state, revision,
and reconciliation-required flag. Decimal values are stored as text to avoid
SQLite floating-point rounding.

The lifecycle supports preflight pass/rejection, capital reservation, durable
submit intent, acknowledgment, partial/full fills, cancel intent/confirmation,
timeout, lost acknowledgment, reconciliation, abort, and final PnL settlement.
`SUBMIT_INTENT` only records a handoff boundary; it does not submit. Likewise,
`CANCEL_INTENT` only records an intent and does not call a venue.

Safety and idempotency rules:

- A lost acknowledgment or timeout while submitting becomes `UNKNOWN` and
  requires reconciliation. A later timeout on an acknowledged or cancel-pending
  order becomes `TIMED_OUT` and also requires reconciliation.
- Neither `UNKNOWN` nor `TIMED_OUT` permits another submit-intent transition.
  A reconciliation result of `NOT_FOUND` remains unknown and does not authorize
  retry. Only recognized venue observations can clear reconciliation-required.
- An exact duplicate intent is idempotent and returns its current persisted
  record. Reusing an intent ID or client order ID with different order data is
  rejected. Replaying the same event ID and payload is idempotent; reusing that
  event ID with changed data is rejected.
- Unknown event, observed, or persisted states fail closed. Illegal transitions,
  overfills, and inconsistent reconciliation quantities are rejected.
- Repository construction requires exactly one explicit SQLite connection or
  path. It never opens `trade_journal.sqlite3` or any production journal by
  default. Tests use temporary paths or in-memory injected connections.

This is persistence and lifecycle-state infrastructure, not a venue integration,
execution authorization, live-trading feature, or proof of an exchange order.
Transport orchestration and venue-specific acknowledgment/reconciliation
remain separate responsibilities.

Focused offline validation:

```powershell
$env:PYTHONPATH = "arb_bot"
python -m py_compile arb_bot\arbx\order_lifecycle.py tests\test_order_lifecycle.py
python -m pytest -q tests\test_order_lifecycle.py
```
