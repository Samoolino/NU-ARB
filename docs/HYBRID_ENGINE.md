# Nu-Arb Hybrid Engine

The Python core remains the authority for normalized market data, depth analysis,
profitability, venue eligibility, risk, execution verification and P&L.

Adapters:
- CCXT Pro: native CEX REST/public WS/private WS/order transport.
- Freqtrade: optional external strategy/process bridge.
- Hummingbot: optional external strategy/process bridge.
- Future chain/DEX adapters: same canonical contracts and gates.

External strategy adapters cannot grant live eligibility.

## Venue validation ladder

CATALOGED -> ADAPTER_AVAILABLE -> REST_OK -> PUBLIC_WS_OK -> PRIVATE_WS_OK ->
BALANCE_OK -> PERMISSION_OK -> EXECUTION_OK -> DEPTH_OK -> LIVE_ELIGIBLE

## Depth certification

Each venue/symbol is checked for fresh bid/ask, receive age, sequence/timestamp when
available, configurable depth, visible quote depth, spread sanity, spot metadata and
order capabilities. Arbitrage calculations walk visible depth rather than trusting
top-of-book prices.

## Strategy base

profit-compounding DCA capital deployment;
cross-exchange spot arbitrage;
intra-exchange triangular arbitrage;
multi-exchange triangular routing;
stablecoin arbitrage;
spot execution/routing.

DCA is capital allocation, not averaging down a losing position. A realized live loss
remains a hard circuit breaker under the current $3 starter policy.

## Twenty-venue candidate set

Binance, Bybit, OKX, KuCoin, Gate.io, Bitget, Kraken, Coinbase, MEXC, HTX,
Bitfinex, Crypto.com, CoinEx, Bitstamp, Gemini, BingX, LBank, WhiteBIT,
BitMart, Upbit.

Catalog membership is not live approval. Runtime evidence decides eligibility.

## Production progression

1. audit: current self-tests and latency probe.
2. scaffold: compile/import contract.
3. validate: read-only venue/account/depth checks.
4. paper: multi-venue live-book paper execution.
5. live-preflight: authenticated eligibility only; zero orders.
6. live: operator-enabled execution after fresh per-venue evidence.

A venue can remain ineligible because its adapter, permissions, private stream,
execution semantics or depth is not currently verifiable.
