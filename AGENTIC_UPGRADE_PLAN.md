# Agentic Upgrade Plan (Paper-First)

## Goal
Add an agent-orchestration layer around the existing deterministic NU-ARB engine without replacing exchange transport, strategy math, risk checks, or the execution journal. This is a staged implementation plan, not evidence that proposed components have been implemented or tested.

## Current architecture observed
- ccxt is declared in arb_bot/requirements.txt; runtime modules import ccxt.pro for WebSocket-capable exchange access.
- arb_bot/arbx/hybrid/adapters.py contains the hybrid adapter layer.
- arb_bot/arbx/worker.py owns exchange-worker behavior.
- arb_bot/arbx/hub.py coordinates workers and strategy/gate/execution components.
- arb_bot/arbx/strategy.py contains opportunity evaluation.
- arb_bot/arbx/gate.py contains ProfitGate and RiskManager.
- EXCHANGE_SUPPORT.md distinguishes adapter availability, scanner eligibility, execution capability, and live eligibility.
- .github/agents/Enter.agent.md is a repository engineering-agent instruction file, not a runtime trading agent.

These findings come from source and documentation inspection. Venue reachability and capabilities require runtime evidence.

## Design principle: agents advise; deterministic code decides
Do not let an LLM call exchange order methods, mutate risk configuration, edit credentials, or bypass existing gates. Agents should emit typed proposals; ordinary Python code validates them and may accept or reject them. Keep the initial implementation paper-only.

## Proposed components
1. Connectivity/health observer: consume adapter health, REST latency, public/private stream timestamps, sequence gaps, reconnect counts, and capability probes. Publish typed HEALTHY, DEGRADED, STALE, or UNAVAILABLE states. Never infer connectivity from a configured venue name.
2. Market-data quality observer: validate timestamps, age, empty/crossed books, sequence continuity where available, symbol identity, quote currency, and visible depth. Invalid or stale snapshots cannot become candidates.
3. Opportunity analyst: retain existing depth-aware strategy math and fee/rebalance assumptions. An optional LLM may summarize why a candidate passed or failed, but must not calculate authoritative edge or alter candidates.
4. Policy/risk gate: retain ProfitGate, RiskManager, inventory checks, rate limits, freshness checks, and circuit breakers as the sole authority. Missing evidence means reject.
5. Paper executor and journal: route accepted candidates to the existing paper executor and durable journal. Record source timestamps, estimated fees, modeled edge, gate result, and simulated outcome.
6. Supervisor/orchestrator: coordinate bounded tasks, timeouts, cancellation, and recovery. It can request re-checks or stop a session; it cannot lower thresholds or submit orders.
7. Human review/reporting: surface health, explanations, unknown capabilities, and rejection reasons. Execution-mode or risk-configuration changes remain separate explicit operator actions.

## Should LangGraph be added?
Not as the first change. The repository already has Python orchestration in Hub and a worker model. First introduce typed observer/proposal interfaces and deterministic tests. If durable multi-step reasoning or resumable approvals later become necessary, evaluate LangGraph around those interfaces, not as a replacement for the engine. Do not put an LLM in the hot path for every order-book update.

## Connector options
- CCXT / CCXT Pro: keep as the default normalized exchange transport already used by this repository. Verify each adapter and capability at runtime.
- Official exchange SDK or native REST/WebSocket adapter: add only for a specific, evidenced CCXT capability gap. Place it behind the existing adapter contract and add tests for authentication, timestamps, order-book normalization, errors, rate limits, and reconnection.
- Hummingbot: a separate strategy/connector framework with its own connectors; consider only if deliberately adopting its engine and connector model. Avoid two independent order managers against the same account.
- NautilusTrader: a larger event-driven trading/backtesting engine; consider as a deliberate architecture migration, not a lightweight agent plug-in.
- Cryptofeed: a market-data feed option, not a replacement for authenticated account/order execution. Review its license and operational fit before use.

Do not combine libraries merely to increase integration counts. Multiple implementations can disagree on symbols, fees, precision, order state, and balances. One adapter must be the authoritative owner of each venue/account/order lifecycle.

## Implementation sequence
### Phase 0 — Baseline and evidence
- Record the current commit, dependency versions, existing test commands, and offline self-test results.
- Inventory the adapter registry and tests.
- Run the offline self-test and unit suite before changing behavior.
- Produce a per-venue capability report from runtime evidence; distinguish public market-data access from authenticated account capability.

### Phase 1 — Typed health and candidate envelopes
- Define immutable schemas for VenueHealth, BookQuality, OpportunityProposal, and GateDecision.
- Attach source venue, symbol, receive timestamp, exchange timestamp, age, and sequence metadata.
- Reject stale/invalid inputs before strategy evaluation.
- Test missing timestamps, stale books, empty depth, symbol mismatch, and venue disconnection.

### Phase 2 — Agent observer interface (no order tools)
- Implement a bounded asynchronous observer that reads health and journal events.
- Observer output is explanatory metadata only; it cannot modify proposals or invoke an executor.
- Test timeout, cancellation, exceptions, and malformed output.
- Keep any optional LLM disabled by default and outside the market-data hot path.

### Phase 3 — Paper-only orchestration
- Send accepted candidates through the existing ProfitGate and paper executor.
- Add idempotent event IDs, bounded queues/backpressure, structured logs, and deterministic replay fixtures.
- Test that rejected or malformed proposals never reach paper execution and observer failure cannot disable existing risk gates.

### Phase 4 — Readiness review
- Verify the configured tests and offline self-tests.
- Run a sustained paper session with real market data and no real orders.
- Compare modeled outcomes with the journal and investigate stale-stream/reconnect behavior.
- Do not treat paper results as proof of real fills or profits.

## Acceptance criteria
- No LLM or agent can call exchange order/transfer methods.
- Existing risk gates cannot be bypassed by observer output.
- Stale, missing, malformed, or contradictory evidence fails closed.
- Venue status is backed by current connectivity evidence, not a registry entry.
- Observer failure cannot crash or silently authorize execution.
- Tests prove rejected candidates never reach the paper executor.
- No live orders, transfers, or credential changes occur during verification.

## Dependencies
No new dependency is required for baseline work or a typed deterministic observer. Defer adding LangGraph or another orchestration package until a tested use case requires it. Keep the existing CCXT dependency until a specific adapter gap is demonstrated and covered by contract tests.