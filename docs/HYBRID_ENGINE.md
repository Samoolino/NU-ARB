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


## Adapter verification contract

Each configured venue is certified independently. The CCXT Pro adapter reports fresh evidence for:

1. **REST** — market load plus exchange server-time request when supported, including measured REST RTT.
2. **Public WebSocket** — live order-book event with bid/ask data, sequence/timestamp when supplied by the venue.
3. **Private WebSocket** — authenticated balance stream with a unified free/used/total snapshot. If `watchBalance` is unavailable, private WS remains unverified; no live approval is inferred.
4. **Authenticated balance** — REST balance snapshot with free/used/total maps.
5. **Permissions** — delegated to `arbx.permissions.inspect_permissions`; an adapter capability flag alone never proves key scopes.
6. **Execution** — spot market, create/fetch order, public book support, and IOC limit support are required for live certification.
7. **Depth** — normalized live order book is walked and checked for freshness, spread and visible quote liquidity for the configured notional.

The resulting evidence is a per-venue certification record. A venue is **LIVE_ELIGIBLE only when every required live check passes**. A failed or unavailable check is retained as a reason and the venue remains disabled.

### Venue-by-venue certification

The read-only PowerShell stage is:

`.scriptsestructure-hybrid.ps1 -Stage venue-verify -Venues binance,bybit,okx -Symbol "BTC/USDT" -Notional 3`

The script tests one venue at a time. It does not submit orders or transfers. This is deliberately separate from production live startup, which still requires the normal multi-venue cross-arbitrage gates.

A single-venue validation uses `BOT_HYBRID_VALIDATION_ONLY=1` only to permit isolated certification. It does **not** relax the production requirement for at least two live venues and explicit cross-live enablement.

### Evidence states

`CATALOGED -> ADAPTER_AVAILABLE -> REST_OK -> PUBLIC_WS_OK -> PRIVATE_WS_OK -> BALANCE_OK -> PERMISSION_OK -> EXECUTION_OK -> DEPTH_OK -> LIVE_ELIGIBLE`

A venue may be cataloged while remaining ineligible. The engine never treats the 20-venue catalog as proof of connectivity, permission, liquidity, or trading readiness.


## Live execution enforcement

The hybrid certification layer is enforced twice in live mode:

1. `hybrid-validate` provides explicit, read-only venue certification.
2. `ExchangeWorker.prepare()` re-certifies the actual venue immediately before constructing the live executor.

A stale or manually asserted certification therefore cannot authorize live trading.

Certification evidence is persisted in the SQLite journal under `venue_certifications`. The record contains venue, symbol, notional, eligibility, failure reasons, and the verification evidence.

## Capital policy

Live trading starts at exactly $3.00. After a completed profitable trade, the realized profit is added to the next allocation. A realized loss immediately halts new engagements. Unfilled IOC attempts do not count as realized losses, but repeated failures still trigger the consecutive-failure halt.

There is no fixed $25 order ceiling and no retired $3 session-loss ceiling.

## Production PowerShell sequence

```powershell
cd C:\path\to\Nu-Arb
.\scripts\restructure-hybrid.ps1 -Stage audit
.\scripts\restructure-hybrid.ps1 -Stage scaffold
.\scripts\restructure-hybrid.ps1 -Stage validate
.\scripts\restructure-hybrid.ps1 -Stage paper -Symbol "BTC/USDT" -Notional 3
.\scripts\restructure-hybrid.ps1 -Stage venue-verify -Venues binance,bybit,okx -Symbol "BTC/USDT" -Notional 3
.\scripts\restructure-hybrid.ps1 -Stage live-preflight -Symbol "BTC/USDT" -Notional 3
```

The venue-verification stage performs no orders or transfers. Live execution remains blocked unless the actual worker's fresh certification passes.
