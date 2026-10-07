# Exchange support and verification limits

**Policy snapshot: 2026-10-07.** This documents implemented permission checks,
not live account verification. No credentials or exchange balances were checked
for this snapshot; per-account eligibility requires fresh local preflight.

The hybrid engine maintains a canonical catalog of exactly 20 candidate identities. A registry entry is only `CATALOGUED`; it is not proof of an adapter, authentication support, market-data verification, execution certification, or live eligibility. The `adapter` field is a preferred transport selector, not capability evidence. These separate states begin `UNVERIFIED` and become eligible only through fresh runtime checks. Verification expires after five minutes; an expired venue is shown as `STALE` and is removed from engine choices until it is checked again.

| Venue | Credential fields shown | Permission verification | Live trading |
| --- | --- | --- | --- |
| Binance | HMAC key + secret; RSA key + PEM private key; Ed25519 key + PEM private key | Signed [key-restrictions endpoint](https://developers.binance.com/en/docs/products/wallet/capital/account/API-key-permission); spot and margin share a trading flag | May qualify when permission flags and fresh preflight pass |
| Bybit | API key + secret | Signed `/v5/user/query-api`; parses scopes, read-only flag, and IP allowlist | May qualify only for exclusive `SpotTrade`, no other scopes, write enabled, and IP allowlist |
| KuCoin | API key + secret + passphrase | Signed `/api/v1/user/api-key`; requires success code `200000`, matching configured key, parseable scopes, and checks the IP whitelist | May qualify only for `General` + `Spot` scopes, no transfer/withdraw scope, and a proven IP allowlist |
| HTX | API key + secret | API-key info response must report `status=ok`, identify the configured key, and expose parseable scope evidence | Disabled: trade scope does not prove spot-only permission or disabled internal/universal transfers |
| MEXC | API key + secret | Signed `/api/v3/account`; validates `permissions`, `canTrade`, and `canWithdraw` response fields | Disabled: account response does not prove an IP allowlist or disabled internal/universal transfers |
| OKX | API key + secret + passphrase | Signed `/api/v5/account/config` permission and IP fields | Disabled: returned trade scope is not spot-specific |
| Bitfinex | API key + secret | Signed [current key-permissions endpoint](https://docs.bitfinex.com/reference/key-permissions); reports current key read/write scopes | Disabled: `orders` is not spot-specific and this endpoint does not prove IP restriction |
| Gate.io, LBank, Bitget, Kraken, Coinbase Exchange, Bitstamp, Gemini, Crypto.com Exchange, CoinEx, BingX, WhiteBIT | API key + secret (some venues also require a passphrase) | Authenticated REST/balance/stream checks; no supported scope probe | Disabled until a venue-specific probe proves safe spot scope, disabled withdrawals/transfers, and IP restriction |

The canonical identity set is Binance, Bybit, OKX, KuCoin, Gate.io, Bitget, Kraken, Coinbase Exchange, MEXC, HTX, Bitfinex, Crypto.com Exchange, CoinEx, Bitstamp, Gemini, BingX, LBank, WhiteBIT, BitMart, and Upbit. The control API and frontend catalog represent all 20 identities, but only 18 have supported control-engine selection/verification routes. BitMart and Upbit remain catalogued with `controlApiStatus=UNAVAILABLE`; they are shown as disabled catalog-only entries and are rejected if submitted to verification or engine-start endpoints. No unavailable entry is substituted with a different venue.

`controlApiStatus=AVAILABLE` means only that an identity has a control API
selection route. It does not establish adapter availability, successful
authentication, market-data connectivity, execution support, or live
eligibility. The public scanner's selectable registry also remains 18 venues;
its static catalog metadata does not enable the two unavailable identities.

Registry metadata separately reports `CATALOGUED`,
`PUBLIC_MARKET_VERIFIED`, `PUBLIC_WS_VERIFIED`, `AUTHENTICATED`,
`BALANCE_VERIFIED`, `PRIVATE_STREAM_VERIFIED`, `PERMISSIONS_VERIFIED`,
`EXECUTION_ROUTE_VERIFIED`, and `LIVE_ELIGIBLE`. Every identity starts at
`CATALOGUED`; unobserved stages remain false/unverified and engine selection
remains unavailable until fresh verification evidence makes a supported venue
eligible. Existing control preflight evidence does not certify an actual order
route, so `EXECUTION_ROUTE_VERIFIED` is not inferred from adapter capability
flags.

The API key and private material stay in the encrypted control-service database and are never returned to the browser. Binance RSA and Ed25519 PEM material is mapped to CCXT's `secret` field as text at the transport boundary; CCXT's signer then encodes it for cryptographic loading. The Ed25519 path is covered by an ephemeral-key signing test that performs no network request or order. Permission probes report evidence; only the strict Binance, Bybit, and KuCoin checks can potentially permit live execution. Bybit requires an explicit successful `retCode`; `10010` is recorded as an IP-allowlist blocker. OKX and Bitfinex return the common validation schema but remain policy-blocked because they cannot prove all required spot-only, transfer-disabled, and IP-restriction conditions. MEXC and HTX can produce validated permission-probe evidence, but their current response fields likewise do not prove all required restrictions. KuCoin is marked verified only after the success code, configured key identity, and permission scopes validate; missing IP restriction or unsafe scopes still block live. A list of IP ranges covering the entire IPv4 or IPv6 space is not accepted as restricted. If the installed adapter does not advertise `fetchTime`, `watchOrderBook`, or `watchBalance`, or if a real message does not arrive, verification fails and the venue does not enter the authenticated scanner.

For repeated authenticated permission checks, `python run.py permission-revalidate [venue ...]` persists a credential-free report after each attempt. With no venue arguments, it checks all seven supported permission-probe venues: Binance, Bybit, KuCoin, HTX, MEXC, OKX, and Bitfinex. It retries transient network/time-out/rate-limit failures with exponential backoff (30 seconds up to 15 minutes by default), honors Ctrl+C, and stops a venue when its evidence is validated or a terminal auth/adapter/policy blocker is returned. A successful probe that is policy-blocked is recorded as `VALIDATED_POLICY_BLOCKED`; the retry loop does not pretend that repeated requests can create missing exchange permissions. This command checks permission endpoints only: it does not query balances, open private streams, submit orders, or enable live mode.

`python run.py authenticated-feed-validation BTC/USDT [venue ...]` is the stricter connected-scanner check. Each selected venue must first return complete permission evidence, then the exact same active spot pair must return valid REST and WebSocket books; bid/ask levels must be finite, positive, correctly sorted, and not crossed. Transient connectivity errors retry with bounded exponential backoff, with each attempt durably reported. Policy failures stop without weakening the requirements. The scan is read-only and does not query balances or place orders. A strict pair pass is feed evidence only, not a live-permission grant or profit assurance.

`FULLY_VERIFIED` describes a fresh successful authenticated connectivity check, including both WebSocket message checks. It does **not** mean that an exchange has verified trading permission. The UI reports `liveEligible` separately. Seven venues currently have permission probes; only Binance, Bybit, and KuCoin can potentially pass the strict live-permission policy. MEXC and HTX can have validated scope evidence yet remain live-ineligible because the evidence cannot prove every required restriction. Binance's API combines spot and margin into one flag. Paper sessions use real market and private-balance streams with virtual execution. The public Vercel scanner is a separate read-only view.

### Persistent readiness state and strategy scope

Run `python run.py readiness-state` from `arb_bot` after the public randomized
feed audit, `permission-revalidate`, and (using fresh, locally rotated
credentials) `authenticated-feed-validation`. It writes timestamped and
`live-readiness-state-latest.json` reports under `diagnostics`. The report
accepts only read-back-validated artifacts with the expected schema/scope and
marks evidence older than 15 minutes stale. It never reads credentials, account
databases, balances, engine switches, or submits orders. Therefore, this report
always remains observational and cannot itself declare a venue live eligible;
live account balance, private stream, IOC route, and current risk/capital
evidence remain separately required. Public-feed success, a saved permission
response, or a strategy catalog entry alone never qualifies a venue.

The six historic strategy catalog labels do not all represent independent live
execution routes:

| Catalog entry | Actual engine meaning | Live connection state |
| --- | --- | --- |
| `cross_exchange` | Implemented cross-venue IOC execution route | Fail-closed; needs two separately verified eligible venues, pre-funded inventory, streams, route and risk evidence, plus explicit cross-live switches |
| `triangular_intra_exchange` | Implemented same-venue triangular IOC route | Fail-closed; needs one eligible venue, all three active spot markets, inventory, streams, route and risk evidence, plus explicit triangular-live switches |
| `dca_profit_compounding` | Capital-allocation policy, not an order route | Policy only; relies on known realized PnL and risk approval |
| `stablecoin_arbitrage` | Helper calculation that reuses cross-exchange math | No separately certified execution route |
| `triangular_multi_exchange` | Catalog label | No distinct multi-venue triangular executor |
| `spot` | Market type | Not an arbitrage strategy |

The strategy catalog and the runtime execution gate are different layers.
Only the cross-exchange and same-venue triangular route names map to order
authorization; even those routes have **not** been live-verified merely by
being implemented or listed. The readiness artifact emits per-venue source
freshness and explicit strategy route status so stale/missing evidence is not
mistaken for a connected live engagement.

Venue acceptance has distinct levels:

- `adapterAvailable`: the installed CCXT Pro package exposes an adapter. This does not prove the venue is reachable or that an account is connected.
- `scannerEligible`: fresh REST/account/balance checks and real private-balance plus public-order-book WebSocket messages succeeded. This is the authenticated scanner level, not permission to trade.
- `executionEligible`: the requested spot market is valid and adapter metadata declares `createOrder`, `createMarketOrder` (needed for triangular emergency unwind), `fetchOrder`, and IOC limit support. Order-book depth is normalized for venue constraints (Bybit, HTX, and Bitfinex do not accept the generic depth of 10). MEXC Pro order-book decoding requires the pinned `protobuf==5.29.5` runtime dependency. These checks do not prove the exchange will accept an order for a particular account or symbol.
- `liveEligible`: fresh scanner evidence, execution capability, and the strict venue-specific permission requirements all pass. Unknown or partial permission evidence is always false.

### Sandbox multi-venue path

Paper mode can compare live books from every configured worker that successfully prepares. With two or more workers, it finds common spot symbols, evaluates both buy/sell directions using visible depth, subtracts each venue's taker fee and the configured rebalance haircut, then sends candidates through `ProfitGate`. The gate rejects stale books, latency failures, rate limits, insufficient edge, worst-case results below the configured floor, exchange-minimum failures, and inventory/balance failures. Only candidates that pass are recorded through the paper executor; no exchange order or transfer is sent.

For the terminal sandbox, set `BOT_MODE=paper` and `BOT_EXCHANGES` to the venues to compare, then run `python run.py --headless`; `python run.py selftest` exercises the cross path with fake venues offline. In the authenticated dashboard, each selected paper venue must also pass the fresh account/private-stream checks. A detected opportunity or simulated paper profit is not a live permission grant and does not guarantee that a later real execution will profit.

The production live path repeats the preflight for every selected venue. Live cross-exchange execution additionally requires at least two `liveEligible` venues, the explicit `cross_live` opt-in, pre-funded balances on both sides, and the operator flag. Missed or uneven fills can still require an unwind or manual recovery at a loss; the worst-case floor applies to completed fills within IOC limits, not all execution outcomes.

For cross-venue comparison, the engine tests visible depth breakpoints, caps size at the configured trade notional and available quote/base inventory, and ranks eligible candidates by largest modeled net dollar floor, then expected net and capital utilization. It serializes cross execution, refreshes the chosen venues' balances, and recomputes from the latest books immediately before the gate. Ranking, balances available, depth, book sequence/age, and latency are journaled; inspect with `python run.py opportunities`. These estimates are not guaranteed profits: cross-exchange orders are non-atomic and actual fees/fills/unwinds can differ. The current guarded terminal launcher starts at $3 allocation, compounds realized gains, and halts new engagements after a realized loss; it has no fixed $200 profit target or guaranteed loss ceiling.

## Current product boundaries

- The browser supports account creation, login, exchange credential entry, evidence display, recent verified balance snapshots, paper/live controls, and reading the durable execution journal.
- While an engine session runs, the status panel exposes actual recent book messages, sequence/timestamp data where CCXT supplies them, private-stream message age, REST latency, clock drift, and degraded state. The engine stops a protected session if its authenticated stream goes stale, and live mode also stops on stale public market data or latency above its pause threshold.
- The profit target is a numeric USD net-realized target for the current engine session. The engine halts new opportunities once its journaled realized PnL reaches that target. The journal endpoint also reports completed realized PnL across durable saved sessions; this lifetime total does not change the current-session stop threshold.
- No funds are transferred by the starter-capital view. It displays only the latest authenticated balance data and does not estimate USD valuation without a price source.
- Live trading is operator-disabled by default and starts at $3 starter capital. Realized profit may increase subsequent allocation; realized live loss halts new engagements. There is no averaging down or martingale sizing. Engine requests can name only the 18 venues routed by the control API; BitMart and Upbit catalog entries are rejected. Each selected venue must pass fresh account, private/public stream, latency, execution, balance, and permission checks. A single venue failure blocks the entire selected live set. Live cross-exchange execution also requires at least two eligible venues, explicit `cross_live` opt-in, and the separate operator flag. Transfer planning is read-only; transfers are never automatically submitted.

## Required deployment resources

The control API, its SQLite database, and the persistent WebSocket engine run on Railway. Vercel serves the static dashboard and Node API proxy. The Railway volume, public HTTPS domain, control-service token, encryption key, and Vercel environment variables must be configured in their respective hosting accounts; they are not repository files.


## Hybrid adapter policy

The production core now separates transport adapters from strategy adapters. CCXT Pro is
the canonical CEX transport. Freqtrade and Hummingbot are optional strategy/process
bridges and cannot grant venue live eligibility.

The canonical twenty-venue candidate catalog is the exact identity set listed
above. Coinbase Exchange and Crypto.com Exchange are the display identities;
the existing internal `coinbase` and `cryptocom` IDs are retained for
compatibility. BitMart and Upbit are catalogued only and are explicitly
unavailable in the current web control API. Adapter, authentication,
market-data, execution, and live-eligibility status do not inherit from catalog
membership and remain unverified until separate runtime evidence exists.

Candidate status is not an assurance of live trading. Each selected venue must pass
fresh REST, public WebSocket, private WebSocket, balance, permission, execution and
depth validation. A symbol is also rejected if visible book depth cannot support the
requested notional or if the book is stale/invalid.

Use scripts/restructure-hybrid.ps1 for the production progression:
audit -> scaffold -> validate -> paper -> live-preflight.
live-preflight is read-only and submits no orders or transfers.
