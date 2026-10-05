# LIVE_ENGAGEMENT_AUDIT.md

## Purpose

This document is the continuous live-engagement certification contract for NU-ARB.

A supplied API key is **not** itself a live-trading credential. The system must prove, in sequence, that the saved account can authenticate, expose usable balances and streams, satisfy trade-permission policy, support the required execution primitives, maintain fresh infrastructure evidence, and only then participate in a profitable opportunity.

The certification is account-specific, evidence-backed, expiring, and fail-closed.

## The two dimensions that must never be conflated

1. **Live engagement readiness** — whether an exchange account is currently safe and technically capable of participating in live execution.
2. **Profitability eligibility** — whether a particular opportunity is profitable after fees, slippage, book depth, latency, inventory constraints, and unwind risk.

An account can be LIVE_READY while there is no profitable opportunity. A profitable-looking opportunity can exist while the account is not LIVE_READY. Orders require both.

## Continuous state sequence

`CREDENTIALS_CONFIGURED
→ ADAPTER_AVAILABLE
→ AUTHENTICATED
→ ACCOUNT_VERIFIED
→ BALANCE_VERIFIED
→ MARKET_UNIVERSE_READY
→ PUBLIC_STREAM_READY
→ PRIVATE_STREAM_READY
→ PERMISSION_VERIFIED_OR_ATTESTED
→ EXECUTION_CAPABLE
→ LATENCY_HEALTHY
→ LIVE_ELIGIBLE
→ LIVE_READY
→ OPPORTUNITY_DETECTED
→ OPPORTUNITY_MODELED
→ PROFITABLE_AFTER_RISK
→ EXECUTION_RESERVED
→ EXECUTING
→ FILLS_RECONCILED
→ SETTLEMENT_VERIFIED
→ PNL_VERIFIED
→ TARGET_PROGRESS_UPDATED`

Every arrow is a gate. Failure or evidence expiry moves the account/opportunity back to the last proven safe state; it must not silently retain a stronger state.

## Stage audit

| # | Stage | Required proof | Current NU-ARB behavior | Required improvement |
|---|---|---|---|---|
| 01 | Credentials configured | Encrypted account material exists; auth mode is valid | Stored with AES-GCM and never returned | Keep secret material out of logs/responses; add credential fingerprint for change detection |
| 02 | Adapter available | Correct CCXT Pro adapter can be constructed | `_make_exchange` constructs configured adapter | Persist adapter/version evidence so runtime changes invalidate certification |
| 03 | Authenticated | Authenticated request succeeds | `fetch_balance` is the primary auth/account proof | Separate authentication from account-data success in evidence |
| 04 | Account verified | Exchange returns a coherent private account response | Unified balance snapshot required | Add exchange-specific account identity/permissions where available |
| 05 | Balance verified | Free/used/total balances are coherent and fresh | `fetch_balance` + persisted balance snapshot | Add balance freshness TTL independent of general verification TTL |
| 06 | Market universe ready | Spot markets discovered and normalized | `load_markets` + selected spot symbol check | Certify the complete discovered spot universe, not only BTC/USDT |
| 07 | Public stream ready | Fresh order-book event for the relevant market | `watch_order_book` checked during probe | Make stream freshness/sequence a continuously maintained state, not only a promotion-time snapshot |
| 08 | Private stream ready | Authenticated balance/private stream works | `watch_balance` required for scanner eligibility | Maintain heartbeat/reconnect state and timestamp continuously |
| 09 | Permission verified/attested | Trade permission and withdrawal safety are proven | Machine permission probes for supported venues; explicit operator attestation otherwise | Persist evidence source, timestamp, and scope; invalidate on credential change |
| 10 | Execution capable | IOC/fetch-order/unwind primitives available | Capability checks cover spot/create/fetch/IOC/unwind | Add venue-specific dry-run capability checks that never submit an order |
| 11 | Latency healthy | REST and stream latency remain inside configured thresholds | REST RTT captured; worker latency gate exists | Promote latency to explicit evidence with configurable thresholds and rolling samples |
| 12 | Live eligible | All account-level gates pass simultaneously | `scannerEligible + executionEligible + liveEligible` | Centralize this predicate so no endpoint can implement a weaker version |
| 13 | Live ready | Operator explicitly promotes a fresh account | `LIVE_READY` promotion requires fresh reconnect | Store promotion evidence and invalidate on credential/scope/config changes |
| 14 | Opportunity detected | Fresh books imply a candidate path | Hub scans cross-exchange opportunities | Move to event-driven affected-cycle recalculation for lower latency |
| 15 | Opportunity modeled | Depth/VWAP/fees/slippage/limits modeled | Strategy + gate calculate expected/worst case | Add explicit unwind/partial-fill loss model |
| 16 | Profitable after risk | Worst-case net profit remains above configured floor | ProfitGate checks age, edge, worst case, minimum profit, latency | Rank by expected target progress, not raw spread |
| 17 | Execution reserved | Balance/risk/rate-limit/opportunity capacity reserved | Venue locks exist; no durable reservation ledger yet | **P0:** add durable reservation manager with expiry and crash release |
| 18 | Executing | Durable leg state tracks submitted/fill/partial/failure | Existing executor/journal records execution result | **P0:** make execution state crash-recoverable and leg-aware |
| 19 | Fills reconciled | Exchange-confirmed fills/orders are authoritative | Current settlement uses executor result | **P0:** reconcile exchange order/fill data before declaring FILLED |
| 20 | Settlement verified | Final balances/positions match expected post-trade state | Balance refresh exists at account layer | Add post-trade balance delta and residual inventory verification |
| 21 | PnL verified | Realized net PnL agrees with fills/fees | Journal records result and compares modeled floor | Calculate PnL from confirmed fills + actual fees, not only executor result |
| 22 | Target progress | Target uses verified realized PnL | Hub updates target progress and halts at target | Persist target checkpoint and make restart recovery authoritative |

## Truth hierarchy

When states conflict, use the strongest available evidence:

1. Exchange-confirmed order/fill/balance
2. Authenticated private stream
3. Authenticated REST
4. Public order-book stream
5. Discovery/ticker data
6. Strategy/model estimate

A weaker source must never overwrite a stronger source with a stronger state.

## Continuous invalidation rules

The following events must immediately prevent new live orders:

- credential decryption/authentication failure;
- private stream disconnect or stale heartbeat;
- public book staleness for the selected opportunity;
- verification TTL expiry;
- permission evidence expiry or credential-scope change;
- insufficient free inventory;
- exchange latency above configured threshold;
- execution capability regression;
- risk/session-loss limit reached;
- target already reached;
- unresolved prior partial fill or settlement exception;
- control-service/engine preflight failure.

Existing open orders must enter a separate recovery/reconciliation path; they must not be treated as cancelled merely because a local gate failed.

## API-key supplied flow

When an operator supplies an API key:

1. Store only encrypted credentials.
2. Construct the venue adapter.
3. Authenticate and retrieve balances.
4. Discover/normalize spot markets.
5. Establish public and private streams.
6. Verify trade permission and withdrawal safety, or require explicit operator attestation where machine-readable scopes are unavailable.
7. Verify execution capabilities.
8. Record timestamped evidence.
9. Promote the account to LIVE_READY only after all required account gates pass.
10. Continuously refresh the account while LIVE_READY.
11. At live-engine activation, re-check every selected account from fresh persisted evidence.
12. At opportunity time, independently re-check book freshness, balances, latency, limits and modeled profitability.
13. Reserve resources before submission.
14. Submit and persist each leg state.
15. Reconcile exchange-confirmed fills.
16. Verify settlement and realized PnL.
17. Update target progress from verified PnL.
18. Continue only if every next-state predicate remains true.

## Current audit verdict

**Account certification:** strong foundation, but not yet a complete continuous certification system.

**Profitability gate:** strong modeled pre-trade controls, but profitability remains separate from account readiness and must stay that way.

**Live execution safety:** fail-closed at the control-plane boundary, with explicit operator confirmation and multi-venue requirements.

**Highest-priority structural upgrades:** durable reservations, crash-recoverable leg state, exchange-confirmed fill reconciliation, typed/expiring evidence, complete market-universe certification, and target-aware opportunity ranking.

The engine should never advance because code reached the next function. It advances only because the evidence required by the next state is true **now**.


## Verified progression status — 2026-10-05

- **P0-1 durable reservations: VERIFIED.** Atomic multi-resource SQLite reservations, expiry/recovery, release, inventory/execution/rate admission, and regression tests pass CI.
- **P0-2 crash-recoverable execution: VERIFIED.** Live cross-exchange executions persist before submission; leg state survives process restart; unresolved live executions are recovered as a hard activation block; regression tests pass CI.
- **Live activation admission: VERIFIED.** Activation readiness and live engine start now reject unresolved prior live execution state before any new live order can be admitted.
- **Actual exchange engagement: PENDING REAL CREDENTIALS.** No real API key has been certified and no live order has been submitted. When credentials are entered, the system must independently prove authentication, balances, market/stream health, permission safety, execution capability, freshness and latency before LIVE_READY.
