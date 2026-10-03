# NU-ARB

ARBX is a Python spot-arbitrage bot for triangular and cross-exchange strategies. The application source is in [`arb_bot/`](arb_bot/), and the operating guide is in [`ARBX_README.md`](ARBX_README.md).

## Vercel web dashboard

The repository root includes a Vercel dashboard for public, read-only spot order-book scans. Choose two venues, a USDT-quoted symbol, a quote-currency notional, and estimated taker fees. The serverless API loads market metadata through CCXT, requests each selected order book, walks the visible depth, and estimates both buy/sell directions after the entered fee rates.

The venue picker is configured for Binance, Bybit, OKX, KuCoin, Gate.io, MEXC, HTX, LBank, Bitget, Kraken, Coinbase Exchange, Bitfinex, Bitstamp, Gemini, Crypto.com Exchange, CoinEx, BingX, and WhiteBIT. Actual market availability depends on whether both selected exchanges list the requested spot pair and whether their public API is reachable from the deployed region. The dashboard shows per-venue errors and does not claim unavailable adapters or markets are live.

For Vercel, keep the **Other** Framework Preset (`framework: null` in `vercel.json`). Set Root Directory to the repository root (`.`), not `arb_bot`. Leave Build Command and Output Directory unset/default. Vercel serves `public/index.html` at `/`, and the Node.js functions remain at `/api/scan` and `/api/control`. `.vercelignore` excludes the Python engine and its requirements from Vercel while preserving them in GitHub for Railway. The public scanner needs no environment variables; the account panel needs the control service configuration below.

The account panel creates accounts and verifies saved exchange credentials using authenticated REST, an actual private `watchBalance` WebSocket event, and a live public order-book WebSocket message. Scanner eligibility requires both account REST and private/public WebSocket checks. The control service also exposes paper/live engine controls backed by the existing Python risk gates and SQLite execution journal. Live order submission is operator-disabled by default and currently eligible only for Binance keys whose signed permissions response proves spot trading enabled, withdrawals/transfers disabled, IP restriction enabled, and whose private stream passes fresh preflight. No order is placed by the permission check itself.

### Enable the account panel

See [the exchange support matrix](EXCHANGE_SUPPORT.md) for the credential fields, verification evidence, and venue-specific live-trading limits. Adapter availability and successful authentication are checked at runtime; a venue name in the picker is not a completed integration.

Railway runs the durable Python control service separately from Vercel. The root `railway.json` selects `Dockerfile.control`, configures the `/healthz` health check, and retries failed service starts. Connect the repository to a Railway service with the service root set to the repository root so the Dockerfile can copy `arb_bot/` files. Attach a persistent volume mounted at `/data`, generate a Railway HTTPS domain, and keep one replica while the service uses SQLite.

Set these Railway service variables:

- `ARBX_APP_DB=/data/arbx_app.sqlite3`.
- `APP_SECURE_COOKIE=1`.
- `ENGINE_PROXY_TOKEN`: a long random service-to-service secret.
- `CREDENTIAL_ENCRYPTION_KEY`: a stable 32-byte key encoded as 64 hex characters. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` and back it up offline. Do not rotate it after credentials have been stored unless you migrate those credentials; changing it makes them unreadable.
- `ARBX_LIVE_TRADING_ENABLED=0` initially. Keep it disabled until HTTPS, storage, exchange permissions, and the live preflight are verified.

In Vercel Production, set `ARBX_CONTROL_API_URL` to the Railway HTTPS origin (for example `https://arb-control-production.up.railway.app`) and set `ENGINE_PROXY_TOKEN` to the same sensitive secret used in Railway. Redeploy after setting these variables. Keep Preview disconnected from production secrets; use a separate test service and token if account flows need Preview testing.

For Binance live eligibility, configure stable Railway outbound IPs and allowlist each one on the exchange API key. The service verifies spot permission, disabled withdrawals/transfers, IP restrictions, and fresh private/public WebSocket health before enabling live mode. The panel still requires the operator to enable live mode and the user to type `I ACCEPT REAL ORDERS`; order size is capped at $25, the loss stop at $5, and a profit target is required. The engine does not auto-resume a live session after restart. No credentials belong in GitHub.

Railway volumes, the Railway public domain, Vercel project settings, and environment secrets are account-level resources; the repository config cannot create them.

The standalone CLI continues to support `BOT_TARGET_PROFIT_USD` and `BOT_JOURNAL_PATH`; engine sessions started from the account panel pass an explicit per-session profit target and use persistent per-account journal files under `/data`. See [the target and journal guide](ARBX_README.md#global-realized-profit-target-and-durable-journal).

Run the dependency-free scanner unit tests with Node.js 20 or later:

```sh
npm test
```

## Windows setup

From the repository root, create and activate a virtual environment, install the dependencies, and run the offline self-test:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r arb_bot\requirements.txt
Set-Location arb_bot
python run.py selftest
```

To run with live market data and virtual funds, stay in `arb_bot` and run:

```powershell
$env:BOT_MODE = "paper"
python run.py --headless
```

The bot defaults to paper mode. Live mode can place real exchange orders; it requires exchange credentials, and cross-exchange live orders require the separate `BOT_CROSS_LIVE=1` opt-in. Arbitrage is risky and profits are not guaranteed. Read the full [operations guide](ARBX_README.md) before configuring exchanges. Never commit API keys.

