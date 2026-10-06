# Persistent Opportunity Finder

Nu-Arb now includes a persistent, scanner-only opportunity layer at
arbx.hybrid.opportunity_finder.

## Sources

For every configured/connected venue and selected symbol the finder maintains:

1. CCXT Pro WebSocket order books when the adapter exposes a live order-book stream.
2. REST order-book snapshots as an independent corroboration path.
3. Authenticated REST balances for the connected API key, used as capital-availability corroboration rather than an inferred balance.
4. REST timing measurements and WS receive age.
5. Optional signed timing webhooks on localhost. A webhook observation is only accepted when its signature is valid (if a secret is configured) and its event timestamp is fresh.
6. Existing Nu-Arb PnL/risk gates. The finder cannot authorize an order by itself.

## Opportunity model

The scanner walks actual bid/ask depth and calculates executable VWAP instead of using only the first book level. It records:

- executable base quantity;
- gross cross-venue spread;
- fees;
- depth slippage;
- REST/WS divergence;
- order-book imbalance;
- microprice pressure;
- short-window price velocity;
- measured latency;
- observed volatility;
- available quote balance;
- Monte Carlo probability of positive net PnL;
- Monte Carlo p05/p50/p95 outcomes;
- expected and worst-case Nu-Arb PnL.

The Monte Carlo layer is an uncertainty estimator, not a profitability guarantee. The live executor remains fail-closed.

## Anticipation

Anticipation is ranking only. A candidate receives a higher score when:

- executable depth spread is positive;
- positive-net probability is high;
- book imbalance supports the route;
- microprice pressure supports the expected direction;
- short-window velocity is favorable;
- latency is low and fresh;
- WS and REST observations agree;
- sufficient connected-key quote balance is observed.

A candidate is persisted as an opportunity even when rejected, so operators can study missed opportunities.

## Webhook endpoint

The module exposes POST /<source> with JSON containing an optional event_ts_ms/timestamp and sequence. When a secret is configured in WebhookLatencyRegistry, send X-NuArb-Signature: sha256=<HMAC-SHA256(raw-body)>.

The current runner binds to 127.0.0.1:8765 by default. Put a controlled authenticated reverse proxy in front of it if an external feed must reach the process. Do not expose the raw listener publicly.

## Run

Set-Location "C:\path\to\NU-ARB"
.scripts\run-opportunity-finder.ps1 -Symbols "BTC/USDT,ETH/USDT" -WebhookPort 8765

The process is intentionally independent of order submission. It can be run alongside the execution engine, and its journal records can be consumed by the dashboard or later execution coordinator work.

## Safety boundary

The finder does not:

- guarantee no-loss trades;
- submit orders;
- bypass VenueAdapter certification;
- infer private-key permissions from configuration;
- treat a webhook as proof of exchange execution;
- replace final live depth/latency/balance checks immediately before an order.

A live order must still pass the native VenueAdapter capability/evidence gates, expected and worst-case PnL gates, route validation, and realized-loss circuit breaker.


## Multi-network scope

The scanner now carries a 20-network settlement catalog covering Ethereum, Bitcoin,
BNB Smart Chain, Solana, Polygon, Arbitrum, Optimism, Avalanche C-Chain, Base,
Tron, XRP Ledger, Cardano, Sui, Aptos, NEAR, Cosmos/IBC, Polkadot, Litecoin,
Dogecoin and TON.

These are **settlement-network candidates**, not claims of direct on-chain
connectivity. For each connected exchange key, the scanner inspects the exchange's
currency/network metadata and records recognized deposit/withdrawal availability.
A network becomes actionable only when the connected exchange/API key exposes it.

## Live route verification

A positive scanner candidate is not emitted to the live opportunity callback until
Nu-Arb re-runs the VenueAdapter certification for both sides of the route. The
verification includes REST, public WS, private WS, balance, permission, execution
and depth evidence. This creates the chain:

scanner -> depth candidate -> fresh REST/WS certification -> live-eligible route -> execution coordinator.

The verification is performed immediately before the callback and therefore remains
subject to the existing execution PnL/risk gates.
