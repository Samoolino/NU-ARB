# Live Engagement Verification Contract

## Purpose
Live engagement is account-specific proof that a saved exchange credential is safe enough to enter live engine preflight. It is not a profitability claim and it does not itself authorize an order.

## Engagement modes
| Mode | Meaning | Live runtime? |
|---|---|---|
| NOT_CONFIGURED | No saved credential | No |
| CONNECTED_UNVERIFIED | Adapter/credential exists but authenticated probes are incomplete | No |
| FULLY_VERIFIED | Fresh authentication, account, balances, scanner, and execution evidence; permission may still be unproven | No |
| LIVE_READY + livePermissionMode=verified | Fresh technical evidence plus machine-readable exchange permission evidence | Yes, subject to global gates |
| LIVE_READY + livePermissionMode=operator_attested | Fresh technical evidence plus explicit operator trade-only attestation where machine scope proof is unavailable | Yes, subject to global gates |
| STALE | Previously verified evidence exceeded its TTL | No |
| VERIFICATION_FAILED | Required probe failed | No |
| ADAPTER_UNAVAILABLE | Runtime adapter unavailable | No |

IMPORTANT: LIVE_READY is account-specific, not venue-global.

## Verification sequence
1. Credentials configured: required fields present and encrypted at rest.
2. Adapter available: supported adapter and authentication mode can be constructed.
3. Authenticated: saved credential successfully authenticates.
4. Account verified: authenticated private endpoints return usable account data.
5. Balance verified: authenticated balance endpoint returns a usable free-balance map; live runtime also requires a private balance stream.
6. Market/scanner eligible: spot markets load and required candidate markets/cycles can be discovered.
7. Execution capable: required order APIs, precision/minimum handling, and IOC limit support exist.
8. Latency healthy: REST preflight is within the live threshold.
9. Public stream ready: live order-book WebSocket is producing fresh books.
10. Private stream ready: authenticated balance WebSocket is producing current snapshots.
11. Permission: prefer machine-readable exchange permission proof; otherwise require exact operator attestation: I CONFIRM TRADE-ONLY API KEY.
12. Fresh evidence: verification has a finite TTL; refresh failure never extends an old TTL.
13. Connectivity heartbeat: activation requires two consecutive read-only authenticated connectivity probes per participating account; each probe revalidates account/balance access, scanner/execution/live eligibility, and private balance-stream connectivity when supported. The persisted heartbeat is TTL-bound and does not authorize orders.
14. LIVE_READY promotion: reconnect and repeat the required probes; promotion never submits an order.

## Permission modes
### verified
The exchange adapter can independently prove live-trading permission through an exchange-specific permission probe.

### operator_attested
The exchange does not expose sufficient reliable machine-readable scope proof. The operator explicitly confirms that the saved key is trade-only, withdrawals are disabled, and IP restriction is applied.

This does NOT mean exchange permission was verified. It means technical readiness is verified and permission scope is operator-attested.

## What LIVE_READY does not mean
- It does not enable the global live-trading flag.
- It does not submit an order.
- It does not bypass profit/risk gates.
- It does not bypass fresh books, balances, latency, or durable reservations.
- It does not bypass unresolved-execution recovery.
- It does not permit withdrawals or transfers.
- It does not certify profitability.

## Global live activation
Live start requires every selected venue to be LIVE_READY, fresh, scanner/execution/live eligible, connected by the persisted read-only heartbeat, and free of unresolved prior live execution state. Runtime preflight then requires authenticated private streams, fresh public books, acceptable latency, and all risk gates. Only after that do the global operator flag and exact I ACCEPT REAL ORDERS confirmation matter.

Live cross-exchange mode requires at least two independently LIVE_READY accounts with fresh connectivity evidence. Transfers/rebalancing remain outside the atomic trade loop.

## Continuous invalidation
Evidence is a TTL-bound assertion, not a permanent permission grant. Authentication failure, private-stream loss, stale books, execution-capability regression, balance failure, permission invalidation, excessive latency, unresolved execution state, session-loss limits, target attainment, or connectivity heartbeat expiry must regress/stop the live path.

## Engagement vs opportunity
Engagement answers: can this account/venue safely participate in live runtime now?
Opportunity eligibility answers: is this particular trade profitable after fees/slippage and safe to execute now?
A venue can be LIVE_READY while every opportunity is rejected. A profitable-looking opportunity can also be rejected because its account is not LIVE_READY.

## Evidence hierarchy
Exchange-confirmed order/fill/balance > authenticated private stream > authenticated REST > public order book > discovery ticker > theoretical model.

## Runtime contract
The control plane persists state, last_verified, evidence, operatorAttestedLive, livePermissionMode, balances, balanceRefreshedAt, masked key identity, and a per-account connectivity heartbeat (consecutive successes, last success/failure, and probe evidence). The runtime receives livePermissionMode through ExchangeCfg and enforces it during live preflight.

## Safety boundary
No verification mode authorizes a live order by itself. The intended progression remains P0-1 durable reservations -> P0-2 crash-recoverable execution -> P0-3 exchange-authoritative fill reconciliation -> P0-4 settlement/balance/PnL reconciliation -> target-aware ranking -> persistent connectivity proof.

CI proves the control logic; it does not certify a real exchange account.
## Verification status
This contract is software-level verification. Actual exchange certification occurs only when a real account is connected and fresh probes pass in the running control plane. Connectivity evidence is a bounded proof of current authenticated reachability, not a guarantee of uninterrupted network service.
