# ARBX agentic upgrade: advisory intelligence, deterministic execution

## Repository review (2026-10-19)

The repository is already a hybrid engine, not a bare CCXT script. `arb_bot/requirements.txt` pins `ccxt>=4.5.66,<5` and includes protobuf, FastAPI/Uvicorn, cryptography, and platform-specific event-loop packages. `docs/HYBRID_ENGINE.md` describes CCXT Pro as the current REST/public/private WebSocket transport; CCXT REST is a fallback and cannot alone satisfy live stream requirements. `arb_bot/arbx/hybrid/adapters.py` defines a `VenueAdapter`-backed transport boundary, while `hybrid/contracts.py`, `requirements.py`, `router.py`, `pnl.py`, `execution.py`, and `arbx/permissions.py` hold normalized contracts, capability checks, PnL/risk decisions, execution states, and permission evidence. `worker.py` and `execute.py` remain important production boundaries. Do not replace these with an LLM.

The current repository catalog contains 20 venues, but catalog presence is not proof of connectivity. `EXCHANGE_SUPPORT.md` correctly separates adapter availability, REST/auth checks, real public/private WebSocket evidence, balances, permission scope, execution capability, depth, and live eligibility. It notes strict permission probes for Binance, Bybit, and KuCoin; other venues remain ineligible where required evidence is missing. The repository README also says the public scanner is read-only and live execution requires explicit operator gates.

## What an agent does (and does not do)

Agents do not replace CCXT, native SDKs, or exchange WebSockets. Those are protocol/connectivity transports. Agents can coordinate diagnosis, explain health degradation, prioritize a bounded set of already-computed observations, summarize venue-specific failures, and propose configuration changes for operator review. A model must not calculate the authoritative fill price, permission status, final PnL, or authorization to submit an order.

Recommended separation:

1. **Transport adapters**: CCXT Pro first for supported async REST/WebSocket paths; CCXT REST only where streaming is not required. Add a native venue SDK behind `VenueAdapter` only when a required capability is missing or evidence shows a concrete reliability/latency benefit. Candidate native clients to evaluate per venue include official SDKs such as Binance Connector, Bybit pybit, OKX SDK, and KuCoin SDK; check each project's current maintenance, API coverage, signing model, rate limits, and license before adoption. Never silently fail over after an ambiguous order acknowledgement; reconcile order ID/status first.
2. **Deterministic market-data pipeline**: normalize timestamps, sequence IDs, symbol metadata, lot/tick/min-notional constraints, depth, and fees. Reject stale/out-of-order books and clock drift. Use bounded async concurrency, backpressure, heartbeat, reconnect with jitter, and circuit breakers.
3. **Opportunity calculator**: deterministic depth-walking and venue-specific fee/cost model. Compute expected net and worst-case net from actual available depth, fees, slippage, inventory, rebalancing/transfer assumptions, and partial-fill reserve. Never treat a top-of-book spread as guaranteed profit.
4. **Agentic supervisor (advisory)**: consume sanitized structured observations only. It may rank which diagnostics to run or explain rejection reasons. It cannot hold API secrets, instantiate exchange clients, set `live_eligible`, change limits, or call order methods.
5. **Policy and execution authority**: existing engine-owned permissions, risk gates, fresh preflight, route validation, worker, execution coordinator, and journal remain authoritative. Fail closed if any evidence is missing. Live mode remains explicit and operator-controlled; begin with read-only and paper validation.
6. **Independent verification**: tests for stale books, reconnects, partial fills, duplicate acknowledgements, rate limits, venue minimums, permission uncertainty, and agent prompt injection. Compare results against the current deterministic engine before any promotion.

## Combining multiple agents

Use one supervisor with narrow specialist roles, not a free-for-all group that can submit orders:

- **Connectivity diagnostician**: summarizes adapter health and failures; no secrets or order methods.
- **Market-data quality reviewer**: flags stale, crossed, unsynchronized, or low-depth observations; cannot rewrite the source evidence.
- **Opportunity explainer**: explains the deterministic calculator's output; cannot produce authoritative PnL.
- **Risk/policy reviewer**: reads policy results and points out missing evidence; cannot approve around a failed gate.
- **Operator report writer**: produces audit-friendly incident and session summaries.

All agents should emit versioned JSON matching schemas. The deterministic supervisor validates schema, provenance, timestamp, and bounded output size. Conflicts or timeouts yield `UNKNOWN/REJECTED`, never implicit approval. Agents should not share credentials or unrestricted tools. No agent gets a tool that can place/cancel orders.

## Initial implementation in this branch

`arb_bot/arbx/agentic/decision.py` adds a dependency-free, advisory-only validation function for a normalized observation. It rejects missing numeric evidence, stale observations, insufficient edge, unsynchronized books, stale venue evidence, and a failed engine risk gate. `ADVISORY_PASS` is explicitly not execution permission. This is a seam for later orchestration; it is intentionally not wired to live order submission and adds no new dependency.

## Connectivity discovery and certification

For every exchange, verify these separately and retain timestamped evidence:

- package/adapter imports and version;
- public REST market metadata/server-time request;
- public WebSocket order-book message with timestamp/sequence when available;
- authenticated REST balance/account response;
- private WebSocket account/order event where supported;
- explicit API-key scope and IP restrictions from an authoritative venue endpoint where available;
- market-specific order capabilities, tick/lot/min-notional, IOC semantics, rate limits, fees, and order-status reconciliation;
- depth freshness and sufficient visible liquidity for a configured test notional.

A generic SDK `connect()` or package installation is not a successful certification. Keep per-venue status as `CATALOGED`, `ADAPTER_AVAILABLE`, `REST_OK`, `PUBLIC_WS_OK`, `PRIVATE_WS_OK`, `BALANCE_OK`, `PERMISSION_OK`, `EXECUTION_OK`, `DEPTH_OK`, and `LIVE_ELIGIBLE`. Evidence expires and must be refreshed before use. An unknown permission scope remains ineligible.

## Implementation sequence

1. **Audit (no behavior change)**: run `python run.py selftest`, the current pytest suite, `npm test`, compile/import checks, and a read-only preflight with credentials loaded only from local environment/secret storage. Record baseline.
2. **Agentic advisory seam**: add typed input/output schemas and deterministic fail-closed validation (this branch); test missing/stale/contradictory observations. Do not wire agent output into the execution worker.
3. **Read-only diagnostics**: allow the supervisor to request bounded health probes, never order operations; redact secrets and cap concurrency/timeouts.
4. **Paper replay**: replay captured market events, compare calculator outputs with baseline, inject stale streams and partial-fill scenarios, and verify the journal.
5. **Venue-specific transport additions**: add one adapter at a time behind the existing contract, only where a concrete capability gap is proven. Certify with real read-only evidence and sandbox where supported.
6. **Human-reviewed change proposals**: agent may propose thresholds/config diffs, but only deterministic validation plus operator approval can apply them.
7. **Promotion gate**: no live rollout until independent code review, tests, paper soak, per-venue permissions verification, recovery drills, and explicit operator approval. Do not claim that a positive modeled edge assures profit.

## Checks to run locally

From `arb_bot/` with the project's venv activated:

```powershell
python -m pytest ..\tests\test_agentic_decision.py ..\tests\test_hybrid_engine.py
python run.py selftest
python -m compileall arbx
```

From repository root:

```powershell
npm test
```

This branch adds no external agent framework or exchange SDK dependency. Add LangGraph only if multi-step stateful orchestration is needed after the deterministic seam is tested; it should coordinate advisory workflows, not replace venue adapters or risk gates. Pydantic is already not required by the new module; the first seam intentionally uses standard-library dataclasses and mappings to avoid dependency churn. OPA can be considered later if policy grows beyond the existing Python permission/risk layer, but duplicating policy engines before defining ownership risks inconsistent decisions.
