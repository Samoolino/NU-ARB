# Strategy, Hybrid Engine, and Exchange Operating Contract

**Status:** normative design clarification; documentation-only change. This file does not certify a venue, change runtime behavior, enable live trading, or promise profit.

## 1. Authority and mode boundaries

The Python Nu-Arb core is authoritative for normalized market data, strategy evaluation, fee/depth calculations, risk gates, venue certification, execution state, and realized P&L. CCXT Pro is a transport adapter, not an authority for strategy or permissions. Optional Freqtrade/Hummingbot integrations may propose or relay signals but cannot bypass Nu-Arb gates.

| Mode | Permitted activity | Orders/transfers | Required evidence |
|---|---|---|---|
| `selftest` | Offline fake-venue logic tests | None | All relevant tests pass |
| `paper` | Public books; configured account/balance checks where selected; virtual fills | None | Data freshness and paper-execution records |
| `validate` / `venue-verify` | Read-only REST, public/private WS, balance and permission probes | None | Per-venue evidence with timestamps and failure reasons |
| `live-preflight` | Read-only live eligibility evaluation | **Zero orders and zero transfers** | Every release gate below passes |
| `live` | Only explicitly authorized, eligible spot execution | Orders only; no automated withdrawal/transfer path | Fresh per-venue certification, operator opt-in, risk gate, post-trade reconciliation |

A catalog entry, installed adapter, successful public ticker, paper profit, or `FULLY_VERIFIED` connectivity status is not equivalent to trading permission. Certification is per account, venue, market and time; stale or incomplete evidence fails closed.

## 2. Strategy definitions and decision rules

All profitability figures are estimates until exchange fills and fees are reconciled. There is no guaranteed-profit or no-loss trade. Fees, latency, partial fills, inventory constraints, precision, minimum notional, market movement and recovery/unwind can cause losses.

### A. Triangular arbitrage (one venue)

- Find three-market cycles from configured start assets (currently stablecoin starts such as USDT/USDC).
- Walk visible order-book depth for each leg; apply each market's taker fee, precision, minimum amount/notional and IOC limit tolerance.
- Evaluate the full cycle and the modeled worst-case floor before any leg is submitted.
- Live execution must verify each fill before proceeding to the next leg. If a leg fails or only partially fills, enter the explicit recovery/unwind state and halt new work when configured risk rules require it.
- Do not infer profitability from top-of-book prices alone.

### B. Cross-exchange spot arbitrage

- Compare the same spot asset pair across independently certified venues in both buy/sell directions.
- Subtract fees on both sides and configured rebalance/inventory haircuts; use depth and available pre-funded balances, not just quoted price differences.
- Live cross-venue mode requires at least two `liveEligible` venues, explicit `cross_live` and operator enablement, fresh books/balances, and sufficient inventory on both venues.
- The two legs are not atomic. One-sided fills, stale prices and later rebalancing can lose money. No on-chain or exchange transfer is part of the execution loop.

### C. Multi-exchange triangular routing

This is a separately gated routing problem, not a synonym for ordinary cross-exchange arbitrage. It may be considered only when every participating venue and route edge has valid spot metadata, fresh depth, compatible assets/fees, inventory and execution support. Any unverified route segment rejects the whole route. No route may rely on a withdrawal or inter-exchange transfer to complete an atomic trade.

### D. Stablecoin arbitrage

Treat stablecoin pairs as ordinary spot markets with additional depeg, liquidity, venue, and conversion-risk exposure. Do not assume a stablecoin is worth exactly USD 1 for P&L. Use executable depth, actual quote-asset valuation, fees, minimums and an explicit spread/depeg risk haircut. Disable the route if price feeds or quote valuation are missing or stale.

### E. Profit-compounding DCA policy

For the current live policy, “DCA” means staged capital allocation using **realized profits only**; it does not mean averaging down into a losing position. Live starter capital is fixed at **$3.00**. Only verified realized profit may increase the next allocation when compounding is enabled. A realized live loss trips the configured hard circuit breaker and stops new engagements. This policy limits continued activity after a loss; it cannot prevent a loss from occurring. Actual order notional must also respect free balance, venue minimums, precision and risk limits.

### F. Strategy selection order

1. Reject unavailable/stale market data, missing fees, invalid spot symbols, unsupported order semantics, or inadequate depth.
2. Reject venues without fresh certification appropriate to the requested mode.
3. Calculate executable depth-walked results after all known fees/haircuts and venue constraints.
4. Apply modeled expected-edge and worst-case floor thresholds.
5. Apply inventory, rate, latency, consecutive-failure and circuit-breaker checks.
6. Select the best remaining candidate by modeled net dollar outcome and capital utilization; use expected return only as a tie-breaker, not as a substitute for absolute risk checks.
7. Recheck immediately before execution; reconcile exchange-reported fills, fees and balances afterward. On discrepancy, halt and record the reason.

If no candidate passes every gate, the correct strategy is **no trade**.

## 3. Hybrid engine contract

The engine pipeline is:

`Catalog -> Adapter available -> REST health -> Public WS book -> Private WS balance (when required) -> Authenticated balance -> Permission evidence -> Execution capability -> Depth certification -> Strategy/risk gate -> Execution -> Fill reconciliation -> Journal`

- **Python core:** sole owner of canonical contracts, eligibility and risk decisions.
- **CCXT Pro:** default transport for REST and public/private WebSocket paths. An installed class is only adapter availability.
- **CCXT REST fallback:** useful for read-only checks and supported paper tasks; not enough for production live eligibility when required WebSocket evidence is absent.
- **Native/Freqtrade/Hummingbot bridges:** isolated integrations; cannot set `liveEligible` or skip the core gates.
- **Control API/UI:** may request a mode or display evidence, but may not override server-side live disablement, permissions, or engine checks.
- **Persistence:** current control-plane and journal code include SQLite-specific paths. The PostgreSQL migration schema is only a foundation until an adapter, migration runner, both persistence paths, and tests are implemented. Do not claim PostgreSQL-backed runtime until that work is merged and tested.
- **Audit:** each certification and execution decision should retain venue/account identity (without secret material), symbol, mode, timestamp/expiry, check results, reasons, latency samples, strategy inputs, gate outcome, order/fill IDs, fees, and realized P&L. Never log API secrets or private-key bytes.

## 4. Exchange registry and eligibility

The intended catalog has **20 candidate venues**: Binance, Bybit, OKX, KuCoin, Gate.io, Bitget, Kraken, Coinbase Exchange, MEXC, HTX, Bitfinex, Crypto.com Exchange, CoinEx, Bitstamp, Gemini, BingX, LBank, WhiteBIT, BitMart and Upbit.

Catalog membership means only “candidate known to the product.” It does not mean an adapter is installed, a credential is valid, the endpoint is reachable, the account has permission, or live execution is supported. Older documentation/tests refer to 18 venues; reconcile all registries, API responses, UI selectors and tests to one source of truth before calling the catalog consistent.

### Certification meanings

- `CATALOGED`: known venue ID.
- `ADAPTER_AVAILABLE`: installed transport exposes a matching adapter.
- `REST_OK`: required read-only REST/market metadata and server-time checks pass.
- `PUBLIC_WS_OK`: a real fresh public order-book message arrives and parses.
- `PRIVATE_WS_OK`: an authenticated balance message arrives and parses, where required.
- `BALANCE_OK`: authenticated REST balance snapshot is valid and adequate.
- `PERMISSION_OK`: venue-specific API-key scopes/restrictions are positively established; unknown evidence fails.
- `EXECUTION_OK`: required spot order methods and IOC semantics are supported by the adapter and market metadata.
- `DEPTH_OK`: live depth meets freshness, spread, liquidity, precision and minimum-notional requirements for the intended notional.
- `LIVE_ELIGIBLE`: all required preceding checks pass for the specific venue/account/market and evidence is fresh.

Connectivity `FULLY_VERIFIED` is not synonymous with `LIVE_ELIGIBLE`. Permission probes described by existing support documentation indicate only Binance, Bybit and KuCoin may potentially satisfy the current strict permission policy; all others remain live-ineligible until a supported venue-specific probe proves the required restrictions. Do not weaken that policy merely to make more venues appear green.

### Per-venue verification record

For each venue record: canonical ID, adapter ID, adapter/package version, credential presence (never credential value), symbol, market type, server time/RTT, public WS message receipt, private WS message receipt, REST balance check, permission evidence source and fields, order-method capability, IOC support, depth result, failure reasons, checked-at timestamp, expiry, and final `scannerEligible` / `executionEligible` / `liveEligible` booleans.

Use three explicit outcomes:
- **PASS:** required evidence is present and current.
- **FAIL:** a check ran and did not meet requirements.
- **UNKNOWN:** a check is unsupported, timed out, missing, or inconclusive. For live eligibility, UNKNOWN behaves as FAIL.

Verification should expire after the configured short TTL (the support docs currently state five minutes). Expiry removes the venue from eligible engine choices until it is rechecked. A failed venue should not take down independent paper/read-only workers, but it must not silently participate in a live route.

## 5. Latency and market-data requirements

Latency is measured from the actual deployment host to each exchange endpoint; it must not be inferred from geography or documentation.

- Record REST RTT distributions (p50/p95 and sample count), server-clock skew where supported, WebSocket receive age, reconnect count/duration, event-to-evaluation time, and order-submit-to-ack/fill timing when orders are explicitly authorized.
- Existing configuration defaults include max book age **250 ms**, max REST RTT **80 ms**, and a pause threshold of **150 ms**. These are policy thresholds, not measured performance or a promise that a route will be profitable.
- Missing server-time support, an empty latency sample, repeated WS reconnects, stale books, clock drift, or out-of-order/invalid depth should be visible as unknown/failure rather than “healthy.”
- Collect evidence separately for every venue and network path. A healthy REST endpoint does not prove healthy public WS, private WS, or order execution.

## 6. Live release gates (all required)

Live stays disabled unless all are true:

1. Unit and integration tests pass, including negative tests for missing permission evidence, stale certification, unsupported IOC, stale books, partial fills and failed reconciliation.
2. Registry counts, IDs and capability metadata are consistent across docs, config, API, UI and tests.
3. PostgreSQL adapter and migration runner cover both control-plane and journal persistence, with migration/rollback and integration tests; no SQLite-only assumption remains on the production path.
4. Docker Compose paper environment starts cleanly with PostgreSQL health checks, app readiness checks, persistent volumes, non-root containers and no secret baked into the image.
5. Paper runs show repeatable market-data processing and simulated execution, while clearly labelling paper results as non-live.
6. Each candidate live venue has fresh, recorded per-account permission, REST, public WS, private WS, balance, execution and depth evidence.
7. A separate explicit operator approval enables live mode; default and missing configuration remain disabled.
8. Pre-trade gates, order idempotency, bounded retries, partial-fill recovery, reconciliation, emergency halt and audit journaling are tested.
9. No automated withdrawal/transfer capability is granted to the trading process.
10. A reviewed deployment report identifies unverified checks and residual risks. No checklist item may be replaced by a simulated success or documentation claim.

## 7. Known clarification debt

- Reconcile 18-vs-20 venue counts from one canonical registry.
- Reconcile older README claims and any UI copy with current $3 starter / realized-profit compounding / realized-loss circuit-breaker policy.
- Confirm actual behavior of every PowerShell stage before running it against credentials; stage names alone are not proof that a command is read-only.
- PostgreSQL schema exists as a foundation only; app integration and tests remain outstanding.
- Runtime venue, latency, Compose, PostgreSQL and live-execution evidence has not been established by this documentation change.

## 8. Operator reporting format

Every audit or preflight report should end with:
- **Mode tested:** selftest / paper / read-only validation / live-preflight.
- **Orders/transfers sent:** exact count; expected zero outside explicitly approved live execution.
- **Venues:** cataloged, adapter available, scanner eligible, execution eligible, live eligible, failed, unknown.
- **Latency:** per-venue samples and p50/p95; explicitly mark missing measurements.
- **Persistence/deployment:** backend actually used, migrations applied, container/test results.
- **Strategy:** candidate type, modeled edge/floor assumptions, fees/haircuts, risk-gate result; never present modeled profit as realized.
- **Blockers:** specific failing/unknown checks and next safe action.
