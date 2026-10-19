# Production-readiness gate: NU-ARB / ARBX

This runbook is a release gate, not a claim that the system is production-ready. Keep live trading disabled until every required item has fresh, reviewable evidence. Never put API keys, secrets, signatures, private payloads, or full account identifiers into CI logs, issues, or chat.

## 1. Restore CI and capture evidence

The Python and dashboard jobs must start on actual runners (non-empty runner name, non-zero runner ID, and populated step summaries). If jobs fail before their first step, inspect GitHub Actions/repository runner availability and billing/permissions; do not make code changes based on a missing log.

Required green checks:
- Python compile.
- Complete Python suite: `PYTHONPATH=arb_bot python -m pytest -q tests`.
- Offline paper self-test: from `arb_bot/`, set `BOT_MODE=paper` and `ARBX_LIVE_TRADING_ENABLED=0`, then run `python run.py selftest`.
- Node dashboard suite: `npm test` with Node.js 20 or later.

CI uploads JUnit and self-test diagnostics when a runner starts. A green compile alone is not a test pass.

## 2. Reproduce locally in paper mode

From the repository root in WSL/Linux:

```sh
python -m pip install -r arb_bot/requirements.txt pytest
python -m compileall -q arb_bot
PYTHONPATH=arb_bot python -m pytest -q tests
(
  cd arb_bot
  BOT_MODE=paper ARBX_LIVE_TRADING_ENABLED=0 PYTHONPATH=. python run.py selftest
)
npm test
```

Run the commands separately and retain exit codes and sanitized logs. The paper self-test uses fake exchanges and must not require network access or API keys.

## 3. Recorded-event replay and recovery

The deterministic regression suite includes a recorded depth fixture and checks:
- Fresh order-book evidence passes validation; stale evidence is rejected.
- Partial fill is represented as partial, followed by a reconciliation state.
- Unreconciled partial fills do not contribute to realized PnL; the reconciled fill record survives SQLite close/reopen.
- Non-finite profitability, PnL, or fill quantities fail closed.

Before release, additionally replay representative real recorded events in a non-production environment and document results for disconnect/reconnect, stale/future timestamps, sequence gaps, zero/partial fills, cancel timeout, ambiguous order acknowledgement, restart with open orders, and journal recovery. No uncertain order outcome may be marked complete without querying authoritative order/fill state and reconciling inventory.

## 4. Execution safeguard review

Confirm with tests and code review that:
- Missing/unknown permission evidence never becomes live-eligible.
- Stale public depth, stale private stream, incomplete balance snapshots, or unhealthy latency prevent execution.
- Risk/profitability thresholds and loss stops remain engine-owned; agent reports cannot override them.
- Order errors, partial fills, and uncertain outcomes halt or enter reconciliation rather than being assumed filled.
- Withdrawals and transfers are disabled for any key considered for live execution; key scopes and IP restrictions are verified from the exchange's authenticated permission endpoint.
- No agent/supervisor path can create, cancel, or amend orders.

## 5. Read-only exchange preflight

Run the repository's `python run.py preflight` in the configured local environment for each intended venue and symbol. Do not send credentials to CI. Capture only sanitized evidence:
- REST authentication/connectivity result and observation time.
- Balance response shape and required free inventory (redact exact balances if sharing is unnecessary).
- A real public order-book WebSocket message, sequence/timestamp, and measured receive age.
- A real authenticated private-balance/order stream message and measured receive age.
- Canonical permission response fields: spot trading enabled, withdrawals/transfers disabled, and IP restrictions enabled.
- Adapter-declared execution capabilities and the exact venue/symbol tested.

A public market-data response or a green fake-exchange test does not prove authenticated access, balance availability, private-stream freshness, or permissions for the user's actual key. Where a venue's permission endpoint is unsupported or returns unknown evidence, mark that venue ineligible; do not infer support from its catalog entry.

## Release decision

Do not merge/release until all automated checks are green on a functioning runner, recorded-event replay and recovery drills pass, and each configured venue has fresh independent evidence for every required capability. Keep live trading disabled until an operator completes this review. Passing tests is not execution authorization or a profitability guarantee.
