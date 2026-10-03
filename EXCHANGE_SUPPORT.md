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

## Current product boundaries

- The browser supports account creation, login, exchange credential entry, evidence display, recent verified balance snapshots, paper/live controls, and reading the durable execution journal.
- The profit target is a numeric USD net-realized target for the current engine session. The engine halts new opportunities once its journaled realized PnL reaches that target. The journal endpoint also reports completed realized PnL across durable saved sessions; this lifetime total does not change the current-session stop threshold.
- No funds are transferred by the starter-capital view. It displays only the latest authenticated balance data and does not estimate USD valuation without a price source.
- Live trading is operator-disabled by default. It remains limited to one Binance account and the existing per-trade/session-loss caps. Other venues must not be described as live-trading integrations until their permission verification and execution paths are implemented and validated.

## Required deployment resources

The control API, its SQLite database, and the persistent WebSocket engine run on Railway. Vercel serves the static dashboard and Node API proxy. The Railway volume, public HTTPS domain, control-service token, encryption key, and Vercel environment variables must be configured in their respective hosting accounts; they are not repository files.

