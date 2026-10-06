# Nu-Arb Native Adapter Architecture

## Boundary

CCXT and CCXT Pro are transport implementations, not the Nu-Arb domain model.

The authoritative boundary is:

`Strategy -> Opportunity -> Route Optimizer -> PnL/Risk -> Route Validator -> Execution Coordinator -> VenueAdapter -> Transport`

Transport options are:

- `native`: venue-native SDK boundary; fail-closed until a concrete implementation exists.
- `ccxt_pro`: current production-capable WebSocket/REST transport.
- `ccxt`: REST fallback; it cannot satisfy live private/public WebSocket requirements by itself.
- `hummingbot` / `freqtrade`: strategy/process bridges only; they cannot grant live eligibility.

## Canonical contract

`arb_bot/arbx/hybrid/contracts.py` owns the normalized concepts:

- `VenueInfo`
- `VenueCapabilities`
- `ExecutionRequirements`
- `OrderRequest`
- `VenueEvidence`
- `NormalizedDepth`

Every future adapter must translate exchange-specific semantics into these contracts.

## Capability versus evidence

Capabilities describe what an adapter claims to support.

Evidence describes what Nu-Arb actually verified for the selected account, symbol and transport.

A catalog entry, installed SDK or capability flag never grants live eligibility.

## Route validation

`requirements.py` validates a venue against execution requirements.

`router.py` validates every leg of a route before execution.

A route requiring IOC, private streaming and spot limit execution is rejected if any selected venue cannot satisfy those requirements.

## PnL

`pnl.py` separates:

- expected net PnL;
- worst-case net PnL.

The model accounts for fees, slippage, funding, transfer/network cost, borrow, FX, latency, partial-fill reserve, rebalancing and safety reserve.

The profit gate is a fail-closed filter, not a guarantee of realized profit.

## Execution

`execution.py` introduces an explicit execution state machine:

`PLANNED -> SUBMITTING -> PARTIALLY_FILLED / FILLED -> HEDGING / RECONCILING -> COMPLETED`

Failure and halt states are explicit.

The existing worker remains the final live execution boundary until the coordinator is wired into end-to-end order submission and reconciliation.

## Native migration sequence

1. Keep the existing CCXT Pro path operational behind `VenueAdapter`.
2. Add concrete native SDK adapters one venue at a time.
3. Certify each native adapter independently.
4. Compare native and CCXT Pro evidence on the same symbol.
5. Promote native transport only when its evidence is at least as strong.
6. Retain CCXT Pro as a controlled fallback where policy permits.
7. Never silently switch transports after an ambiguous order acknowledgement; reconcile order identity first.

## Current native status

The native SDK boundary is implemented, but no concrete native execution adapter is claimed live-ready yet. Binance, Bybit and OKX are catalogued with native SDK metadata; their native transports must be implemented and certified before they can be selected for live execution.

This distinction is intentional and prevents an SDK package name from being mistaken for verified trading capability.
