# NU-ARB Runtime Architecture & State Contract

> Canonical structural map for the target-based instant arbitrage engine.
>
> This document defines **what exists, what state it is in, what evidence is required to advance, what can make it regress, and what upgrade is required when a point is incomplete**.
>
> It is an implementation contract, not a claim that any real exchange account is currently certified or that live orders have been submitted.

## 1. Runtime topology

```
Operator Console / Vercel
        |
        v
Control API / Persistent Runtime
        |
        +--> Identity + Session
        +--> Credential Vault
        +--> Exchange Certification
        +--> Live Activation Gate
        +--> Engine Lifecycle
        |
        v
Engine Hub
        |
        +--> Exchange Workers (one per selected venue)
        |      |
        |      +--> Adapter / CCXT Pro
        |      +--> Market Discovery
        |      +--> Global Universe
        |      +--> Hot-Market Selector
        |      +--> Public Order Books
        |      +--> Private Balance Stream
        |      +--> REST Latency / Health
        |      +--> Execution Adapter
        |
        +--> Opportunity / Graph Engine
        +--> Cross-Exchange Scanner
        +--> Risk Gate
        +--> Reservation / Execution
        +--> Settlement / PnL
        +--> Target Progress
        +--> Journal / Telemetry
        |
        v
Persistent State + Operator Event Stream
```

### Runtime boundary

- **Vercel/browser**: operator interface and request proxy. It is not the durable trading worker.
- **Persistent control service**: authentication, encrypted credentials, certification state, activation gates, refresh loop, and engine lifecycle.
- **Engine runtime**: asynchronous workers, order-book processing, strategy evaluation, risk gating, and execution.
- **Exchange**: external source of market/account truth.
- **SQLite/control state**: operational state and evidence; it is not the exchange ledger.
- **No transfer operation belongs inside the atomic trade loop.** Cross-exchange inventory must already be available.

## 2. Canonical state vocabulary

### 2.1 Control-plane states

| State | Meaning | Can advance when | Regression trigger |
|---|---|---|---|
| `NOT_CONFIGURED` | No usable account/key is stored | Valid credentials saved | Credential removed |
| `CONNECTED_UNVERIFIED` | Credentials stored, runtime not yet proven | Auth + market/account probe succeeds | Probe failure |
| `FULLY_VERIFIED` | Fresh authenticated account evidence exists | Scanner + execution + permission evidence passes | TTL expiry/failure |
| `LIVE_READY` | Account has passed explicit live promotion gate | Fresh evidence remains valid | Any required evidence becomes stale/invalid |
| `STALE` | Prior evidence exists but TTL elapsed | Fresh verification succeeds | Time |
| `VERIFICATION_FAILED` | Latest certification attempt failed | New successful probe | Repeated failed probe |
| `ADAPTER_UNAVAILABLE` | Required runtime adapter is unavailable | Adapter becomes available | Adapter/runtime regression |

### 2.2 Engine states

```
STOPPED
  -> PREFLIGHT
  -> STARTING
  -> RUNNING
  -> DEGRADED
  -> STOPPING
  -> STOPPED

Any fatal preflight/runtime failure -> PREFLIGHT_FAILED or HALTED -> STOPPED
```

The exact persisted engine phase may be represented by the control API's current runtime fields; the semantic contract above is authoritative.

### 2.3 Market states

```
DISCOVERED
  -> NORMALIZED
  -> CANDIDATE
  -> HOT
  -> STREAMING
  -> BOOK_FRESH
  -> OPPORTUNITY_ELIGIBLE
```

A market can regress independently:

```
BOOK_FRESH -> BOOK_STALE
STREAMING -> STREAM_ERROR
HOT -> COLD
OPPORTUNITY_ELIGIBLE -> REJECTED
```

### 2.4 Opportunity states

```
DETECTED
  -> MODELED
  -> RANKED
  -> RISK_CHECK
  -> ACCEPTED
  -> RESERVED
  -> EXECUTING
  -> SETTLING
  -> VERIFIED
  -> TARGET_PROGRESS
```

Failure branches:

```
MODELED -> REJECTED
RISK_CHECK -> REJECTED
RESERVED -> RELEASED
EXECUTING -> PARTIAL / FAILED / HALTED
SETTLING -> SETTLEMENT_EXCEPTION
VERIFIED -> PNL_EXCEPTION
```

## 3. Point-by-point execution contract

### P0 — Operator intent / target

**Input:** target profit, trade size, session-loss limit, selected venues, paper/live mode.

**Required state**
- Authenticated operator session.
- Target parameters are valid and bounded.
- Live mode additionally requires cross-live selection and at least two venues.

**Produced state**
- Immutable engine-start request.
- Target configuration becomes the session's optimization objective.

**Current implementation**
- `EngineStart` validates mode, venue uniqueness, trade size, max loss, target profit, private-stream requirement, and cross-live constraints.

**Upgrade requirement**
- Persist a session-level `TargetState` with:
  - target amount
  - realized amount
  - remaining amount
  - session loss
  - elapsed time
  - opportunity count
  - accepted/executed/rejected volume
  - terminal reason
- Make target state durable across process restart.

**Exit:** `TARGET_INITIALIZED`.

---

### P1 — Identity / operator session

**Input:** email/password.

**Required state**
- Valid session token.
- Session not expired.

**Produced state**
- Stable `user_id` for all account and engine ownership checks.

**Current implementation**
- Password hashing and session storage are present.
- Request validation avoids echoing sensitive payloads.

**Upgrade requirement**
- Add explicit session ownership to every engine mutation and every journal query.
- Add audit events for login/logout/session expiry.

**Exit:** `AUTHENTICATED`.

---

### P2 — Credential vault

**Input:** venue API credentials.

**Required state**
- Credentials are encrypted at rest.
- Correct auth mode is selected.
- Credentials are never emitted in API errors/events/UI.

**Produced state**
- Decrypted credentials available only inside the adapter connection boundary.

**Current implementation**
- AES-GCM encrypted credential storage.
- Masked credential presentation.
- Redaction in exchange error handling.

**Upgrade requirement**
- Add key-version metadata for encryption rotation.
- Add explicit credential lifecycle: `ACTIVE / REVOKED / ROTATING / INVALID`.
- Never use credential presence alone as account readiness.

**Exit:** `CREDENTIALS_STORED`.

---

### P3 — Adapter construction

**Input:** venue ID, auth mode, credentials.

**Required state**
- Venue is registered.
- Runtime adapter exists.
- Spot configuration is explicit.

**Produced state**
- Live adapter instance with rate limiting and spot-market options.

**Current implementation**
- CCXT Pro adapter mapping.
- Venue-specific spot options.
- Binance key-mode handling.

**Upgrade requirement**
- Formalize an adapter capability report:
  - discovery
  - public WS
  - private WS
  - balance
  - order creation
  - IOC
  - order status
  - cancel
  - permissions
  - normalization
  - error classification.
- Store capability version/evidence per venue.

**Exit:** `ADAPTER_AVAILABLE`.

---

### P4 — Exchange authentication / certification

**Input:** adapter.

**Required evidence**
- REST connectivity.
- Authenticated account access.
- Balance retrieval.
- Market loading.
- Public/private stream capability.
- Execution capability.
- Permission evidence or explicit operator trade-only attestation where machine-readable scope evidence is unavailable.

**Current implementation**
- `_probe_exchange()`.
- Five-minute verification TTL.
- Explicit `LIVE_READY` promotion.
- Controlled operator attestation path.
- Background refresh for live-ready accounts.

**Upgrade requirement**
- Replace broad evidence dictionaries with typed evidence records:
  `CapabilityEvidence(kind, source, observed_at, expires_at, value, error)`.
- Track every probe independently so one successful probe cannot mask another stale probe.

**Exit:** `ACCOUNT_VERIFIED` or `VERIFICATION_FAILED`.

---

### P5 — Global market discovery

**Input:** authenticated/public adapter market metadata.

**Required state**
- Every active spot market exposed by the venue is indexed.
- Derivatives/contracts are excluded from the spot universe.

**Produced state**
- Canonical `MarketRecord` universe.

**Current implementation**
- `build_market_universe()`.
- Active spot filtering.
- Base/quote normalization.
- Quote-volume fallback calculation.

**Important semantic**
- “All markets” means **discover/index all available spot markets**, not maintain a WebSocket subscription for every market.

**Upgrade requirement**
- Add a first-class canonical asset registry.
- Store market precision, min amount, min notional, fee schedule, status, and source timestamp.
- Detect symbol aliases and delistings.

**Exit:** `UNIVERSE_READY`.

---

### P6 — Candidate market selection

**Input:** global universe.

**Required state**
- Market is theoretically useful for arbitrage.
- Quote asset is compatible with configured capital/start assets.
- Market is active.

**Produced state**
- Candidate universe `U1`.

**Current implementation**
- Worker discovery and triangle construction.
- Hot-market ranking uses quote-volume/liquidity score.

**Upgrade requirement**
- Separate `U1` from strategy-specific triangular cycles.
- Support a unified candidate graph for:
  - triangular
  - cross-exchange
  - multi-hop.
- Rank on executable liquidity, not ticker volume alone.

**Exit:** `CANDIDATE`.

---

### P7 — Hot-market prioritization

**Input:** candidate universe.

**Required state**
- Bounded subscription budget.
- Highest-value markets promoted first.

**Produced state**
- `U2` hot market set.

**Current implementation**
- `select_hot_markets()`.
- Adaptive bounded observation model exists conceptually and in the universe module.

**Upgrade requirement**
- Implement a continuously refreshed heat score:
  `liquidity × volatility × cross_exchange_count × edge_frequency × recent_edge × execution_quality`.
- Add promotion/demotion hysteresis to prevent subscription thrashing.
- Track hot-set reason and score.

**Exit:** `HOT`.

---

### P8 — Public real-time order-book engine

**Input:** hot markets.

**Required state**
- Public WebSocket connected.
- Books contain usable bid/ask depth.
- Book age <= configured maximum.
- Sequence/timestamp integrity is acceptable.

**Produced state**
- Current order book + freshness + sequence evidence.

**Current implementation**
- `MarketData`.
- Dirty-symbol event loop.
- Health snapshots expose latest book and age.

**Upgrade requirement**
- Make book state explicitly versioned:
  `BOOK_VERSION(symbol, venue, sequence, exchange_ts, receive_ts)`.
- Detect sequence gaps and force resync.
- Record per-symbol stale transitions.
- Avoid evaluating on partial/inconsistent book updates.

**Exit:** `BOOK_FRESH`.

---

### P9 — Private account state

**Input:** authenticated account.

**Required state**
- Private balance stream is live where required.
- REST balance refresh succeeds.
- Free/used/total balances are coherent.

**Produced state**
- Spendable balance map with freshness timestamp.

**Current implementation**
- `watch_balance()`.
- REST refresh.
- Live worker halts on balance refresh failure.
- Control service refreshes saved LIVE_READY accounts.

**Upgrade requirement**
- Introduce an account-state version/sequence.
- Reconcile private stream and REST snapshots.
- Track reserved balance separately from free balance.
- Never allow two opportunities to consume the same balance reservation.

**Exit:** `BALANCE_FRESH`.

---

### P10 — Latency / infrastructure health

**Input:** venue REST and streaming telemetry.

**Required state**
- RTT p50/p95 within configured bounds.
- Clock skew acceptable.
- Public/private streams fresh.

**Produced state**
- Venue execution-health score.

**Current implementation**
- `LatencyGuard`.
- Preflight RTT and clock-skew checks.
- Health snapshot.

**Upgrade requirement**
- Add a per-venue health state machine:
  `HEALTHY / DEGRADED / HALTED`.
- Make opportunity ranking consume the health score.
- Persist latency distributions, not only current p50/p95.

**Exit:** `EXECUTION_HEALTHY`.

---

### P11 — Opportunity graph

**Input:** fresh books + market graph + balances.

**Required state**
- Only affected graph edges/cycles are recomputed after a book event.
- Each leg has executable side, precision, limits, and fee.

**Produced state**
- Candidate opportunity with complete modeled legs.

**Current implementation**
- `discover()`.
- Per-symbol `by_symbol` index.
- Dirty-book selective triangular evaluation.
- Cross-exchange scan hook exists.

**Upgrade requirement**
- Make the graph canonical and strategy-independent.
- Represent every edge as:
  `venue, market, side, price_curve, fee_curve, capacity, freshness`.
- Use the same graph for triangular and cross-exchange routes.
- Persist graph version/hash with each accepted opportunity.

**Exit:** `MODELED`.

---

### P12 — Opportunity simulation / worst-case economics

**Input:** modeled route.

**Required state**
- Depth-aware VWAP.
- Fees.
- Slippage.
- Precision.
- Minimums.
- Limit tolerance.
- Book age.
- Requested trade size.

**Produced state**
- Expected net.
- Worst-case net.
- Net bps.
- Per-leg quantities/prices.
- Execution limits.

**Current implementation**
- `evaluate_triangle()`.
- Worker records expected and worst-case economics.

**Upgrade requirement**
- Make the simulator route-generic.
- Add probabilistic fill/execution probability.
- Model partial fills and unwind costs.
- Return a deterministic `ExecutionPlan` object.

**Exit:** `MODELED` or `ECONOMICALLY_REJECTED`.

---

### P13 — Opportunity ranking / target optimization

**Input:** all modeled opportunities.

**Required state**
- Rank is based on target progress, not raw spread.

**Produced state**
- Ordered opportunity queue.

**Required ranking factors**
1. Expected net profit.
2. Worst-case profit floor.
3. Probability of successful execution.
4. Available executable liquidity.
5. Book freshness.
6. Venue latency/health.
7. Capital utilization.
8. Fees/slippage.
9. Inventory/unwind risk.
10. Target remaining.

**Current implementation**
- Existing strategy chooses best observed triangular opportunity.
- Target controls exist at engine level.

**Upgrade requirement**
- Introduce an explicit `OpportunityScore` and target-progress optimizer.
- Replace “best spread wins” with “best expected target progress subject to risk budget”.

**Exit:** `RANKED`.

---

### P14 — Risk / profit gate

**Input:** ranked opportunity.

**Required state**
- Fresh books.
- Fresh balance.
- Venue health OK.
- Minimums satisfied.
- Expected/worst-case net above thresholds.
- Session loss limit not breached.
- Rate limits available.
- Kill switch not active.
- Live mode additionally requires live eligibility.

**Produced state**
- `ACCEPTED` or deterministic rejection reason.

**Current implementation**
- Mature `RiskGate`.
- Worst-case modeling.
- Fee/slippage/minimum/latency/balance controls.
- Session loss/kill-switch controls.

**Upgrade requirement**
- Centralize all execution predicates into a versioned `RiskDecision`.
- Include every predicate and observed value.
- Make rejection reasons machine-readable and stable.
- Add explicit global risk budget and per-venue risk budget.

**Exit:** `RISK_APPROVED` or `RISK_REJECTED`.

---

### P15 — Reservation / execution admission

**Input:** risk-approved opportunity.

**Required state**
- Balance reserved.
- Opportunity lock acquired.
- Rate-limit budget available.
- Target capacity reserved.
- No conflicting execution uses the same inventory.

**Current implementation**
- Per-worker execution lock exists.
- Balance is refreshed before live order submission.

**Upgrade requirement — HIGH PRIORITY**
- Implement a first-class reservation manager.
- Persist reservation IDs.
- Reserve:
  - account balance
  - opportunity
  - risk budget
  - target capacity
  - venue rate-limit capacity.
- Make reservations idempotent and crash-recoverable.

**Exit:** `RESERVED`.

---

### P16 — Execution state machine

**Input:** reserved execution plan.

**Required durable states**

```
ExecutionAccepted
ExecutionReserved
Leg1Submitted
Leg1Filled / Leg1Partial / Leg1Failed
Leg2Submitted
Leg2Filled / Leg2Partial / Leg2Failed
Leg3Submitted
Leg3Filled / Leg3Partial / Leg3Failed
SettlementVerification
PnLVerification
Completed / UnwindRequired / Halted
```

**Current implementation**
- `LiveExecutor` / `PaperExecutor`.
- IOC capability is checked.
- Execution failures halt risk.
- Journal records outcome.

**Upgrade requirement — HIGH PRIORITY**
- Persist every order intent and exchange order ID before proceeding.
- Make each transition idempotent.
- Recover in-flight executions after process restart.
- Explicitly support partial fill and unwind state.
- Never infer fill from submission success.

**Exit:** `SETTLEMENT_REQUIRED`.

---

### P17 — Post-trade settlement verification

**Input:** exchange order/fill state.

**Required state**
- Every order is reconciled with exchange truth.
- Final balances are observed.
- Fees/fills are known.

**Produced state**
- Verified realized PnL.
- Final asset balances.
- Any residual/unwind obligation.

**Current implementation**
- Settlement/journal hooks exist.
- Balance refresh occurs after live execution.

**Upgrade requirement — HIGH PRIORITY**
- Fetch/reconcile each order until terminal.
- Persist fill-level fees and quantities.
- Reconcile expected vs actual execution.
- Produce a signed/immutable execution record.

**Exit:** `PNL_VERIFIED` or `SETTLEMENT_EXCEPTION`.

---

### P18 — Target progress

**Input:** verified realized PnL.

**Required state**
- Realized PnL is based on exchange-confirmed fills, not theoretical opportunity value.

**Produced state**
- Target progress:
  `realized / target`.
- Remaining target.
- Session loss.

**Upgrade requirement**
- Make target progress event-sourced from verified executions.
- Add terminal conditions:
  - target reached
  - max loss reached
  - operator stop
  - venue failure
  - session timeout.

**Exit:** `TARGET_PROGRESS_UPDATED` or `SESSION_TERMINATED`.

---

### P19 — Journal / observability

**Input:** every state transition.

**Required state**
- No credentials/secrets.
- Correlation ID connects request -> opportunity -> execution -> fills -> PnL.
- Events are timestamped.

**Current implementation**
- Sanitized engine event endpoint.
- Durable execution journal display.
- Runtime health/exchange telemetry.

**Upgrade requirement**
- Add correlation IDs and state-transition events.
- Store:
  - session ID
  - opportunity ID
  - execution ID
  - venue
  - market
  - graph version
  - risk-decision version
  - book sequence.
- Add structured audit trail for every live gate.

**Exit:** `OBSERVED`.

## 4. End-to-end state flow

```
AUTHENTICATED
   |
CREDENTIALS_STORED
   |
ADAPTER_AVAILABLE
   |
ACCOUNT_VERIFIED
   |
UNIVERSE_READY
   |
CANDIDATE
   |
HOT
   |
BOOK_FRESH + BALANCE_FRESH + EXECUTION_HEALTHY
   |
MODELED
   |
RANKED
   |
RISK_APPROVED
   |
RESERVED
   |
EXECUTING
   |
SETTLEMENT_REQUIRED
   |
PNL_VERIFIED
   |
TARGET_PROGRESS_UPDATED
   |
OBSERVED
```

Any required predicate failing causes a **fail-closed regression**, not silent continuation.

## 5. Live activation state machine

```
Registered venue
   |
Credentials configured
   |
Adapter available
   |
Authenticated
   |
Account/balance verified
   |
Scanner eligible
   |
Execution eligible
   |
Permission verified
      |                 \
      |                  \ no machine-readable permission proof
      |                   -> explicit operator trade-only attestation
      |
Fresh evidence
   |
LIVE_READY
   |
Selected for live engine
   |
Activation readiness: >= 2 venues
   |
Operator live flag enabled
   |
Exact I ACCEPT REAL ORDERS confirmation
   |
Per-venue live preflight
   |
RUNNING
```

### Live activation invariants

1. `LIVE_READY` is account-specific.
2. `LIVE_READY` expires when fresh evidence expires.
3. Promotion never submits an order.
4. Readiness never enables the operator live flag.
5. Direct live engine start cannot bypass `LIVE_READY`.
6. Cross-exchange live mode requires at least two ready venues.
7. Private balance evidence is required.
8. Permission evidence is either machine-verified or explicitly operator-attested under the trade-only/no-withdrawal contract.
9. A failing live preflight stops activation.
10. No withdrawal/transfer operation is part of execution admission.

## 6. Current implementation vs required next upgrades

| Priority | Point | Current state | Required upgrade |
|---|---|---|---|
| P0 | Live safety | Strong fail-closed gates | Preserve; add invariant tests |
| P0 | Execution durability | Journal/hooks, but not full crash recovery | Durable order state machine + reconciliation |
| P0 | Reservations | Worker lock + balance refresh | Persistent reservation manager |
| P0 | Settlement | Post-execution refresh/hooks | Exchange-order/fill reconciliation |
| P1 | Target optimization | Target controls exist; selection remains strategy-local | Explicit target-progress opportunity scorer |
| P1 | Canonical graph | Triangular graph + cross scan | Unified multi-strategy graph |
| P1 | Evidence | Boolean evidence dictionary | Typed, individually expiring evidence |
| P1 | Book integrity | Freshness + sequence exposed | Gap detection/resync/versioned book state |
| P1 | Inventory | Balance tracking | Desired inventory + imbalance + rebalance planner |
| P2 | Market universe | Global discovery + hot selection | Canonical asset registry + heat-score feedback loop |
| P2 | Observability | Logs/journal/health | Correlated event/audit model |
| P2 | Persistence | SQLite operational state | Durable event/state model suitable for restart recovery |
| P2 | Adapter contract | CCXT-based implementation | Formal capability interface + certification matrix |

## 7. Truth hierarchy

When two signals disagree, the engine must use this precedence:

1. **Exchange-confirmed order/fill/balance state**
2. **Authenticated private stream**
3. **Authenticated REST**
4. **Public order-book stream**
5. **Ticker/volume discovery**
6. **Theoretical strategy model**

A lower layer can never override a higher layer's failure.

## 8. Non-goals / explicit boundaries

- Discovering all markets does not mean subscribing to every market.
- A registered exchange is not a live exchange.
- `LIVE_READY` is not proof that a real order will fill.
- Operator attestation is not machine permission verification.
- Paper profitability is not live profitability.
- A submitted order is not a filled order.
- A calculated PnL is not realized PnL until exchange state is reconciled.
- Cross-exchange transfers are not part of the atomic arbitrage transaction.
- A green CI pipeline is not account certification.

## 9. Definition of structurally ready

The engine is structurally ready for a controlled live pilot only when:

- at least two real accounts are individually `LIVE_READY`;
- each has fresh authentication, balance, scanner, execution, stream and permission evidence;
- activation readiness reports both accounts;
- the persistent control service is healthy;
- engine preflight succeeds for every selected venue;
- target/risk configuration is valid;
- no kill switch is active;
- the operator explicitly enables live mode;
- execution, settlement, and reconciliation state is durable enough to recover after restart.

**Current repository status:** the software gate is implemented and fail-closed, but this document does not certify any real exchange account. Runtime certification remains an account-specific operation.
