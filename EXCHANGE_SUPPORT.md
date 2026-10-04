# Exchange support and verification limits

The dashboard lists 18 venues as candidates. A registry entry is not proof that a live adapter, a permission check, or order execution works for that venue. The control API reports CCXT Pro adapter availability and per-account verification results at runtime. A venue becomes eligible for the authenticated scanner only after current REST/account/balance checks and real public and private WebSocket messages have all passed. Verification expires after five minutes; an expired venue is shown as `STALE` and is removed from engine choices until it is checked again.

| Venue | Credential fields shown | Permission verification | Live trading |
| --- | --- | --- | --- |
| Binance | HMAC key + secret; RSA key + PEM private key; Ed25519 key + PEM private key | Signed Binance key-scope endpoint | Supported behind operator flag and fresh preflight |
| KuCoin | API key + secret + passphrase | Not implemented | Disabled |
| OKX | API key + secret + passphrase | Not implemented | Disabled |
| Bitget | API key + secret + passphrase | Not implemented | Disabled |
| Coinbase Exchange | API key + secret + passphrase | Not implemented | Disabled |
| Bybit, Gate.io, MEXC, HTX, LBank, Kraken, Bitfinex, Bitstamp, Gemini, Crypto.com Exchange, CoinEx, BingX, WhiteBIT | API key + secret | Not implemented | Disabled |

The API key and private material stay in the encrypted control-service database and are never returned to the browser. Binance RSA and Ed25519 PEM material is passed to CCXT's signing implementation as bytes. If the installed adapter does not advertise `fetchTime`, `watchOrderBook`, or `watchBalance`, or if a real message does not arrive, verification fails and the venue does not enter the authenticated scanner.

`FULLY_VERIFIED` describes a fresh successful authenticated connectivity check, including both WebSocket message checks. It does **not** mean that an exchange has a verified trading permission scope. The UI reports `liveEligible` separately; at present only Binance has an implemented signed permission probe. Paper sessions initiated from the authenticated engine use real market and private-balance streams with virtual execution. The public Vercel scanner is a separate read-only view and does not claim that a market is executable.

Venue acceptance has distinct levels:

- `adapterAvailable`: the installed CCXT Pro package exposes an adapter. This does not prove the venue is reachable or that an account is connected.
- `scannerEligible`: fresh REST/account/balance checks and real private-balance plus public-order-book WebSocket messages succeeded. This is the authenticated scanner level, not permission to trade.
- `executionEligible`: the requested spot market is valid and adapter metadata declares `createOrder`, `createMarketOrder` (needed for triangular emergency unwind), `fetchOrder`, and IOC limit support. Order-book depth is normalized for venue constraints (Bybit, HTX, and Bitfinex do not accept the generic depth of 10). MEXC Pro order-book decoding requires the pinned `protobuf==5.29.5` runtime dependency. These checks do not prove the exchange will accept an order for a particular account or symbol.
- `liveEligible`: fresh scanner evidence, execution capability, and a venue-specific authenticated permission probe all pass. Unknown or partial permission evidence is always false.

### Sandbox multi-venue path

Paper mode can compare live books from every configured worker that successfully prepares. With two or more workers, it finds common spot symbols, evaluates both buy/sell directions using visible depth, subtracts each venue's taker fee and the configured rebalance haircut, then sends candidates through `ProfitGate`. The gate rejects stale books, latency failures, rate limits, insufficient edge, worst-case results below the configured floor, exchange-minimum failures, and inventory/balance failures. Only candidates that pass are recorded through the paper executor; no exchange order or transfer is sent.

For the terminal sandbox, set `BOT_MODE=paper` and `BOT_EXCHANGES` to the venues to compare, then run `python run.py --headless`; `python run.py selftest` exercises the cross path with fake venues offline. In the authenticated dashboard, each selected paper venue must also pass the fresh account/private-stream checks. A detected opportunity or simulated paper profit is not a live permission grant and does not guarantee that a later real execution will profit.

The production live path repeats the preflight for every selected venue. Live cross-exchange execution additionally requires at least two `liveEligible` venues, the explicit `cross_live` opt-in, pre-funded balances on both sides, and the operator flag. Missed or uneven fills can still require an unwind or manual recovery at a loss; the worst-case floor applies to completed fills within IOC limits, not all execution outcomes.

## Current product boundaries

- The browser supports account creation, login, exchange credential entry, evidence display, recent verified balance snapshots, paper/live controls, and reading the durable execution journal.
- While an engine session runs, the status panel exposes actual recent book messages, sequence/timestamp data where CCXT supplies them, private-stream message age, REST latency, clock drift, and degraded state. The engine stops a protected session if its authenticated stream goes stale, and live mode also stops on stale public market data or latency above its pause threshold.
- The profit target is a numeric USD net-realized target for the current engine session. The engine halts new opportunities once its journaled realized PnL reaches that target. The journal endpoint also reports completed realized PnL across durable saved sessions; this lifetime total does not change the current-session stop threshold.
- No funds are transferred by the starter-capital view. It displays only the latest authenticated balance data and does not estimate USD valuation without a price source.
- Live trading is operator-disabled by default and remains bounded by the per-trade/session-loss request limits. Engine requests can name up to four venues; every selected venue must pass a fresh account, private/public stream, latency, execution-capability, and venue-specific permission preflight. Currently only Binance has the permission probe, so other venues remain ineligible and a multi-venue live session cannot yet pass preflight. Live cross-exchange execution additionally requires at least two eligible venues and an explicit `cross_live` opt-in; the operator flag remains a separate required gate. Gate.io and Bitfinex are not covered by the requested live expansion.

## Required deployment resources

The control API, its SQLite database, and the persistent WebSocket engine run on Railway. Vercel serves the static dashboard and Node API proxy. The Railway volume, public HTTPS domain, control-service token, encryption key, and Vercel environment variables must be configured in their respective hosting accounts; they are not repository files.

