# NU-ARB target-based instant arbitrage upgrade

## Runtime contract

NU-ARB separates the global market universe from the real-time observation set.

1. Load and index every active spot market exposed by each verified adapter.
2. Normalize the market into a canonical symbol/base/quote spot record.
3. Rank markets using available quote volume as a discovery signal.
4. Promote a bounded hot set into continuous order-book observation.
5. Re-evaluate only cycles affected by changed books.
6. Keep cross-exchange candidates ranked by modeled worst-case dollar floor, expected net, capital utilization and execution gates.
7. Expose the same runtime state to the operator console.

This means all pairs/tokens is a discovery requirement, not a requirement to hold thousands of WebSocket subscriptions open simultaneously.

## Exchange validity states

The control plane distinguishes NOT_CONFIGURED, FAILED, STALE, authenticated/account-connected, scanner eligible, execution eligible, and live eligible. A venue is never considered live because it exists in the registry. Live eligibility requires fresh authenticated REST/balance evidence, public and private WebSocket evidence, execution capability evidence, and an explicit venue permission probe. Evidence expires after the configured verification TTL.

## Live engagement

The browser is an operator console, not a bypass around risk controls. A live start requires the operator live flag, at least two venues for cross-exchange live, fresh liveEligible evidence for every selected venue, private balance and public order-book streams, IOC execution capability, acceptable latency, explicit cross_live, and explicit I ACCEPT REAL ORDERS. Cross-exchange inventory is pre-funded; transfers remain outside the atomic trade loop.

## Verification

Run Python tests and a browser smoke flow before enabling the live operator flag: account creation/login, exchange verification, fresh eligibility display, paper engine start/stop, target progress, stale/failed venue removal, live rejection with operator flag off, live rejection when a selected venue lacks fresh evidence, and credential-redaction checks.

A green software test suite does not certify an exchange account. Exchange certification remains a runtime, account-specific evidence process.
