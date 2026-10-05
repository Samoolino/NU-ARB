# NU-ARB Live Mode / CCXT / Vercel Execution Clarity

## 1. Hard boundary

NU-ARB has two different runtime planes:

| Plane | Runtime | CCXT role | Trading |
|---|---|---|---|
| Vercel dashboard | Node.js serverless + browser | Standard CCXT public REST adapters | NEVER places orders |
| Live engine | Python service on durable host (Railway/VPS) | CCXT Pro for authenticated REST + WebSocket execution | Can place real orders only after live preflight |

The Vercel /api/scan endpoint is a public, read-only market snapshot service. It uses standard Node ccxt, loads public spot markets, reads public order books, and returns executionEnabled=false. It must never receive exchange private keys.

The Python engine imports ccxt.pro and is the only component permitted to create live exchange orders. Its live path uses authenticated REST, private balance WebSockets, public order-book WebSockets, CCXT capability metadata, venue-specific permission probes, and the existing execution/risk gates.

Therefore: a green Vercel market snapshot is not a live trading connection.

## 2. What live engagement means for every exchange

A venue is not considered live merely because it exists in the 18-venue registry or because its CCXT adapter loads.

Each selected venue must independently pass:

1. Adapter available — installed CCXT Pro adapter exposes the venue.
2. Authenticated REST — production credentials authenticate.
3. Account/balance — authenticated fetch_balance succeeds with a valid free/used/total map.
4. Public WebSocket — a real order-book message arrives for a valid spot market.
5. Private WebSocket — a real authenticated watch_balance message arrives.
6. Latency/clock — live preflight is inside configured limits.
7. Spot market capability — requested market is spot, not a contract.
8. Execution capability — CCXT reports order/fetch-order/market-order support and IOC limit support where required.
9. Permission evidence — the venue-specific API-key scope probe proves the account is safe for the intended spot strategy.
10. IP restriction / withdrawal safety — required venue-specific controls are proven.
11. Freshness — verification is current; stale evidence cannot authorize live execution.
12. Operator authorization — the selected live session is explicitly armed.

The resulting state is: adapterAvailable → scannerEligible → executionEligible → liveEligible.

Only liveEligible=true can enter the live engine. Unknown, partial, stale, simulated, or documentation-only evidence is false, not probably good.

## 3. Current 18-venue engagement matrix

| Venue | CCXT/CCXT Pro adapter | Live permission evidence | Live state |
|---|---|---|---|
| Binance | Supported path | Supported strict probe | Eligible only after account verification |
| Bybit | Supported path | Supported strict probe | Eligible only after account verification |
| KuCoin | Supported path | Supported strict probe | Eligible only after account verification |
| HTX | Adapter path | Partial/broad scope only | BLOCKED |
| MEXC | Adapter path | Partial | BLOCKED |
| OKX | Adapter path | Partial/non-spot-specific | BLOCKED |
| Bitfinex | Adapter path | Partial/non-spot-specific | BLOCKED |
| Gate.io | Adapter path | No strict safe-scope probe | BLOCKED |
| LBank | Adapter path | No strict safe-scope probe | BLOCKED |
| Bitget | Adapter path | No strict safe-scope probe | BLOCKED |
| Kraken | Adapter path | No strict safe-scope probe | BLOCKED |
| Coinbase Exchange | Adapter path | No strict safe-scope probe | BLOCKED |
| Bitstamp | Adapter path | No strict safe-scope probe | BLOCKED |
| Gemini | Adapter path | No strict safe-scope probe | BLOCKED |
| Crypto.com Exchange | Adapter path | No strict safe-scope probe | BLOCKED |
| CoinEx | Adapter path | No strict safe-scope probe | BLOCKED |
| BingX | Adapter path | No strict safe-scope probe | BLOCKED |
| WhiteBIT | Adapter path | No strict safe-scope probe | BLOCKED |

This table describes software verification capability, not the user's account status. A venue becomes actually live-engaged only when its production account is tested from the intended engine host and fresh evidence is recorded.

## 4. Cross-exchange live requirements

Cross-exchange live mode requires:
- at least two independently liveEligible venues;
- BOT_MODE=live;
- BOT_CROSS=1;
- explicit BOT_CROSS_LIVE=1;
- pre-funded quote inventory at the buy venue;
- pre-funded base inventory at the sell venue;
- fresh public and private streams;
- fresh balance;
- latency within live limits;
- current permission evidence;
- execution capability;
- operator authorization.

No blockchain transfer, withdrawal, bridge or exchange transfer is an execution leg.

## 5. The profit / no-loss structure

NU-ARB must not describe the strategy as guaranteeing no loss. No software can guarantee that for non-atomic exchange execution.

The correct structure is a fail-closed modeled-floor + post-trade verification + bounded-session-risk architecture.

### Pre-trade gate

Every candidate must satisfy fresh order book, acceptable latency, configured minimum expected net edge, positive modeled worst-case edge, minimum modeled USD profit, exchange minimum order constraints, sufficient inventory, rate limits, and a risk manager that is not halted.

The worst-case calculation assumes the submitted IOC limit prices are respected and all intended legs fill.

### Execution protection

Live execution uses IOC limit orders. If a later leg does not fill after an earlier leg has filled, the engine stops the normal cycle, attempts a bounded market unwind, raises LegFailure, halts the protected session, and requires human account review.

Cross-exchange legs are non-atomic. Therefore an unwind can realize a loss even when the pre-trade modeled floor was positive.

### Session risk

The current configuration enforces a maximum live order request of $25, a maximum pilot session-loss trigger of $3, a configurable positive modeled minimum floor, a configurable realized-profit session target, consecutive-failure halt, rate limiting, stale-book halt, and degraded-latency halt.

The $3 value is a risk-stop trigger, not a mathematical guarantee that realized losses can never exceed $3. Exchange outages, partial fills, slippage and unwind losses can create exposure before the halt is observed.

### Profit target

The realized-profit target is a stop condition for new opportunities after the journaled session PnL reaches the configured target. It is not a guarantee that the bot will earn that amount.

## 6. Vercel deployment rule

Vercel remains the presentation/public-read layer.

It must not hold exchange API keys, import the Python engine, submit exchange orders, infer liveEligible from public market data, or label a public CCXT snapshot as a live exchange engagement.

The durable control/engine service remains responsible for authenticated exchange engagement.

## 7. Required operator states

Expose these distinct states:

PUBLIC → public market data only
AUTHENTICATED → account credentials verified
FULLY_VERIFIED → REST + private/public WebSocket evidence verified
EXECUTION_ELIGIBLE → requested spot execution capabilities verified
LIVE_ELIGIBLE → permission + safety requirements also verified
ARMED → operator explicitly authorized the selected live set
RUNNING → live engine currently executing
HALTED → risk or health control stopped new execution

Never collapse these states into a single connected badge.

## 8. Evidence rule

Every live engagement should retain venue, non-secret account identifier, verification timestamp, adapter/version, REST result, balance result, public-stream evidence, private-stream evidence, latency/clock measurements, market capability, permission evidence source, IP-restriction evidence, live eligibility decision, and operator/session ID.

Secrets, API keys, private keys and passphrases must never enter GitHub, browser responses or ordinary audit logs.

## 9. Current implementation conclusion

The repository architecture separates the public Vercel scanner from the Python CCXT Pro engine and has the core fail-closed controls.

The operational distinction is:

CCXT adapter support is not exchange engagement.
Authenticated connectivity is not trading permission.
Trading permission is not execution eligibility.
Execution eligibility is not realized profit.
A positive modeled floor is not a no-loss guarantee.

The only valid route to live execution is:

real production account → real CCXT Pro engagement → real evidence → venue-specific permission proof → liveEligible → cross-live preflight → operator arm → bounded live pilot.