# NU-ARB

ARBX is a Python spot-arbitrage bot for triangular and cross-exchange strategies. The application source is in [`arb_bot/`](arb_bot/), and the operating guide is in [`ARBX_README.md`](ARBX_README.md).

## Workspace engineering agent

The repository includes the **Enter** NU-ARB/ARBX engineering agent at [`.github/agents/Enter.agent.md`](.github/agents/Enter.agent.md). VS Code discovers workspace custom agents from `.github/agents`; select **Enter** in the chat agent picker for implementation, investigation, and verification tasks in this repository. Its instructions require preserving existing risk controls, using evidence-based exchange support claims, protecting credentials, and reporting only checks that actually ran. It does not authorize live orders or enable live trading.

## Vercel web dashboard

The repository root includes a public, read-only spot order-book scanner. Select multiple venues and up to 12 `BASE/USDT`, `BASE/USDC`, or `BASE/DAI` pairs (all 18 registered venues are selected by default), then set one USD-equivalent notional, assumed taker-fee BPS, and a minimum estimated net-edge BPS. The scanner loads spot markets and order books, compares every ordered venue pair on every selected market, walks visible depth, and ranks modeled opportunities. USDT, USDC, and DAI are assumed to be worth $1 for comparison; actual fees, balances, execution costs, and fills can differ. Scan results are not trade instructions and never submit orders.

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

The control API supports all 18 registered venues in one selected session; live execution requires at least two venues and explicit `cross_live` selection. Seven venues now have permission-evidence probes, and Binance, Bybit, and KuCoin can potentially meet strict live scope checks. Every selected venue must still pass fresh balance, permission, and stream checks. Cross-venue candidates are compared by depth-aware modeled dollar floor, expected net, and available capital. The $3 session-loss stop and $200 realized-profit target remain configured; neither is a guarantee against losses.

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

For a read-only authenticated account and market-data preflight, configure `BOT_EXCHANGES` and the venue API credentials in the current PowerShell session, then run `python run.py preflight`. Set `BOT_PREFLIGHT_SYMBOL` to choose the spot market (default: `BTC/USDT`). It reports balances, WebSocket evidence, and permission status without submitting orders. A successful scanner preflight does not mean live trading is eligible; unsupported permission checks remain unverified. Keep credentials local and never commit them.

To run with live market data and virtual funds, stay in `arb_bot` and run:

```powershell
$env:BOT_MODE = "paper"
python run.py --headless
```

The bot defaults to paper mode. Live mode can place real exchange orders; it requires exchange credentials, and cross-exchange live orders require the separate `BOT_CROSS_LIVE=1` opt-in. Arbitrage is risky and profits are not guaranteed. Read the full [operations guide](ARBX_README.md) before configuring exchanges. Never commit API keys.
## Live mode / CCXT clarity

See [LIVE_MODE_CLARITY.md](LIVE_MODE_CLARITY.md) for the authoritative separation between the Vercel public scanner and the Python CCXT Pro live engine, the per-exchange live-engagement contract, and the modeled-profit-floor / bounded-risk structure. A public CCXT snapshot is never a live-trading authorization, and a modeled positive floor is not a no-loss guarantee.
