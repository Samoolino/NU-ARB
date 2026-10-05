# Exchange support and verification limits

The dashboard lists 18 venues as **Potential** engagements. Potential means the adapter/venue is registered for discovery and account verification; it is not a live-trading authorization. A registry entry is not proof that a live adapter, a permission check, or order execution works for that venue. The control API reports CCXT Pro adapter availability and per-account verification results at runtime using explicit engagement labels: **Potential**, **Account Verified**, **Execution Capable**, **Verified live-capable**, **Stale**, or **Verification Failed**. A venue becomes eligible for the authenticated scanner only after current REST/account/balance checks and real public and private WebSocket messages have all passed. Verification expires after five minutes; an expired venue is shown as `STALE` and is removed from engine choices until it is checked again.

| Venue | Credential fields shown | Engagement baseline | Verified live-capable path |
| --- | --- | --- | --- |
| Binance | HMAC key + secret; RSA key + PEM private key; Ed25519 key + PEM private key | Potential → account/execution verified | **Yes, conditionally**: strict permission probe + fresh preflight |
| Bybit | API key + secret | Potential → account/execution verified | **Yes, conditionally**: exclusive SpotTrade + safe scopes + IP allowlist + fresh preflight |
| KuCoin | API key + secret + passphrase | Potential → account/execution verified | **Yes, conditionally**: General + Spot only + safe scopes + IP allowlist + fresh preflight |
| HTX | API key + secret | **Potential** → account verification available | **Not currently verified live-capable**: probe does not establish safe spot-only scope |
| MEXC | API key + secret | **Potential** → account verification available | **Not currently verified live-capable**: account flags do not establish all safety restrictions |
| OKX | API key + secret + passphrase | **Potential** → account verification available | **Not currently verified live-capable**: trade scope is not sufficiently spot-specific |
| Bitfinex | API key + secret | **Potential** → permission evidence available | **Not currently verified live-capable**: orders scope/IP safety is insufficient |
| Gate.io, LBank, Bitget, Kraken, Coinbase Exchange, Bitstamp, Gemini, Crypto.com Exchange, CoinEx, BingX, WhiteBIT | API key + secret (some venues also require a passphrase) | **Potential** → connectivity/account verification | **Not currently verified live-capable** until venue-specific permission safety evidence exists |

All 18 registry venues can be selected together (when their adapters and credentials are available), but are not auto-enabled. The API key and private material stay in the encrypted control-service database and are never returned to the browser. Binance RSA and Ed25519 PEM material is passed to CCXT's signing implementation as bytes. Permission probes report evidence; only the strict Binance, Bybit, and KuCoin checks can potentially permit live execution. If the installed adapter does not advertise `fetchTime`, `watchOrderBook`, or `watchBalance`, or if a real message does not arrive, verification fails and the venue does not enter the authenticated scanner.

`FULLY_VERIFIED` remains an internal connectivity result only. The operator UI no longer presents it as live authorization. The engagement label advances separately: `Potential` → `Account Verified` → `Execution Capable` → `Verified live-capable` only when the strict permission gate passes. The registry has 18 venues; seven have a permission-evidence probe, while the rest remain permission-unverified and ineligible for live mode. Binance's API combines spot and margin into one flag. Paper sessions use real market and private-balance streams with virtual execution. The public Vercel scanner is a separate read-only view.

Venue acceptance has distinct levels:

- `adapterAvailable`: the installed CCXT Pro package exposes an adapter. This does not prove the venue is reachable or that an account is connected.
- `scannerEligible`: fresh REST/account/balance checks and real private-balance plus public-order-book WebSocket messages succeeded. This is the authenticated scanner level, not permission to trade.
- `executionEligible`: the requested spot market is valid and adapter metadata declares `createOrder`, `createMarketOrder` (needed for triangular emergency unwind), `fetchOrder`, and IOC limit support. Order-book depth is normalized for venue constraints (Bybit, HTX, and Bitfinex do not accept the generic depth of 10). MEXC Pro order-book decoding requires the pinned `protobuf==5.29.5` runtime dependency. These checks do not prove the exchange will accept an order for a particular account or symbol.
- `liveEligible`: fresh scanner evidence, execution capability, and the strict venue-specific permission requirements all pass. Unknown or partial permission evidence is always false.

### Sandbox multi-venue path

Paper mode can compare live books from every configured worker that successfully prepares. With two or more workers, it finds common spot symbols, evaluates both buy/sell directions using visible depth, subtracts each venue's taker fee and the configured rebalance haircut, then sends candidates through `ProfitGate`. The gate rejects stale books, latency failures, rate limits, insufficient edge, worst-case results below the configured floor, exchange-minimum failures, and inventory/balance failures. Only candidates that pass are recorded through the paper executor; no exchange order or transfer is sent.

For the terminal sandbox, set `BOT_MODE=paper` and `BOT_EXCHANGES` to the venues to compare, then run `python run.py --headless`; `python run.py selftest` exercises the cross path with fake venues offline. In the authenticated dashboard, each selected paper venue must also pass the fresh account/private-stream checks. A detected opportunity or simulated paper profit is not a live permission grant and does not guarantee that a later real execution will profit.

The production live path repeats the preflight for every selected venue. Live cross-exchange execution additionally requires at least two `liveEligible` venues, the explicit `cross_live` opt-in, pre-funded balances on both sides, and the operator flag. Missed or uneven fills can still require an unwind or manual recovery at a loss; the worst-case floor applies to completed fills within IOC limits, not all execution outcomes.

For cross-venue comparison, the engine tests visible depth breakpoints, caps size at the configured trade notional and available quote/base inventory, and ranks eligible candidates by largest modeled net dollar floor, then expected net and capital utilization. It serializes cross execution, refreshes the chosen venues' balances, and recomputes from the latest books immediately before the gate. Ranking, balances available, depth, book sequence/age, and latency are journaled; inspect with `python run.py opportunities`. These estimates are not guaranteed profits: cross-exchange orders are non-atomic and actual fees/fills/unwinds can differ. A live terminal ignition defaults to a $200 realized-net target and rejects a session-loss stop above $3; reaching either target halts new opportunities.

## Current product boundaries

- The browser supports account creation, login, exchange credential entry, evidence display, recent verified balance snapshots, paper/live controls, and reading the durable execution journal.
- While an engine session runs, the status panel exposes actual recent book messages, sequence/timestamp data where CCXT supplies them, private-stream message age, REST latency, clock drift, and degraded state. The engine stops a protected session if its authenticated stream goes stale, and live mode also stops on stale public market data or latency above its pause threshold.
- The profit target is a numeric USD net-realized target for the current engine session. The engine halts new opportunities once its journaled realized PnL reaches that target. The journal endpoint also reports completed realized PnL across durable saved sessions; this lifetime total does not change the current-session stop threshold.
- No funds are transferred by the starter-capital view. It displays only the latest authenticated balance data and does not estimate USD valuation without a price source.
- Live trading is operator-disabled by default and remains bounded by a $25 per-trade request limit and a $3 maximum pilot session-loss setting. Engine requests can name up to all 18 registered venues; each selected venue must pass fresh account, private/public stream, latency, execution, balance, and permission checks. A single venue failure blocks the entire selected live set. Live cross-exchange execution also requires at least two eligible venues, explicit `cross_live` opt-in, and the separate operator flag. Transfer planning is read-only; transfers are never automatically submitted.

## Required deployment resources

The control API, its SQLite database, and the persistent WebSocket engine run on Railway. Vercel serves the static dashboard and Node API proxy. The Railway volume, public HTTPS domain, control-service token, encryption key, and Vercel environment variables must be configured in their respective hosting accounts; they are not repository files.
