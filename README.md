# NU-ARB

ARBX is a Python spot-arbitrage bot for triangular and cross-exchange strategies. The application source is in [`arb_bot/`](arb_bot/), and the operating guide is in [`ARBX_README.md`](ARBX_README.md).

## Vercel web dashboard

The repository root includes a Vercel dashboard for public, read-only spot order-book scans. Choose two venues, a USDT-quoted symbol, a quote-currency notional, and estimated taker fees. The serverless API loads market metadata through CCXT, requests each selected order book, walks the visible depth, and estimates both buy/sell directions after the entered fee rates.

The venue picker is configured for Binance, Bybit, OKX, KuCoin, Gate.io, MEXC, HTX, LBank, Bitget, Kraken, Coinbase Exchange, Bitfinex, Bitstamp, Gemini, Crypto.com Exchange, CoinEx, BingX, and WhiteBIT. Actual market availability depends on whether both selected exchanges list the requested spot pair and whether their public API is reachable from the deployed region. The dashboard shows per-venue errors and does not claim unavailable adapters or markets are live.

To deploy the public scanner, import this GitHub repository in Vercel and set the project root to the repository root. Vercel serves `index.html` and the Node.js function at `/api/scan`; no build command or environment variables are required. The app is also runnable locally with Vercel CLI using `vercel dev`.

The account panel creates accounts and verifies saved exchange credentials using authenticated REST, an actual private `watchBalance` WebSocket event, and a live public order-book WebSocket message. Scanner eligibility requires both account REST and private/public WebSocket checks. The control service also exposes paper/live engine controls backed by the existing Python risk gates and SQLite execution journal. Live order submission is operator-disabled by default and currently eligible only for Binance keys whose signed permissions response proves spot trading enabled, withdrawals/transfers disabled, IP restriction enabled, and whose private stream passes fresh preflight. No order is placed by the permission check itself.

### Enable the account panel

Vercel does not provide persistent storage for the control database or long-running engine. The repository includes `Dockerfile.control` for a separate Railway control/engine service with Railway-managed HTTPS and a [persistent volume](https://docs.railway.com/volumes). Railway volumes mount as root; the container entrypoint fixes `/data` ownership and then drops to the unprivileged `arbx` account. Create one service from this repository, set Dockerfile path to `Dockerfile.control`, attach a volume mounted at `/data`, and set healthcheck path to `/healthz` ([Railway health-check setup](https://docs.railway.com/deployments/healthchecks)). Set these Railway service variables:

- `ARBX_APP_DB=/data/arbx_app.sqlite3`.
- `RAILWAY_DOCKERFILE_PATH=Dockerfile.control`.
- `APP_SECURE_COOKIE=1`.
- `ENGINE_PROXY_TOKEN`: a long random service-to-service token.
- `CREDENTIAL_ENCRYPTION_KEY`: a stable 32-byte key encoded as 64 hex characters. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`; save it in Railway's secret variables and keep an offline backup. Losing or changing it makes stored API credentials unreadable.
- `ARBX_LIVE_TRADING_ENABLED=0` initially. Set to `1` only when the HTTPS domain, durable volume, and venue restrictions are confirmed.
- `RAILWAY_RUN_UID=0`: Railway mounts volumes as root; the container entrypoint then changes ownership and starts Uvicorn as the unprivileged `arbx` user.

Generate the Railway public domain after the service starts; Railway provisions HTTPS automatically ([public networking](https://docs.railway.com/networking/public-networking)). Binance live eligibility requires stable allowlisted egress, so enable Railway [static outbound IPs](https://docs.railway.com/networking/static-outbound-ips) (Pro plan), redeploy, and allowlist each displayed egress IP in the Binance API key settings. A successful signed restrictions call from this same service also confirms the current host can use the key. Configure Vercel Production with:

- `ARBX_CONTROL_API_URL`: the Railway HTTPS origin, such as `https://arb-control-production.up.railway.app`.
- `ENGINE_PROXY_TOKEN`: the same service token stored on Railway. Mark it sensitive.

For example, after linking the repository to the Vercel project, run `vercel env add ARBX_CONTROL_API_URL production` and `vercel env add ENGINE_PROXY_TOKEN production --sensitive`, then deploy with `vercel --prod` ([Vercel environment CLI](https://vercel.com/docs/cli/env)). Redeploy after changing environment variables. Keep Vercel Preview disconnected from production credentials; use a separate test control service and token if Preview account tests are needed. The Vercel proxy accepts HTTPS upstreams only.

Run one Railway service replica while it uses SQLite and the attached volume. The API starts only one engine session per instance. Paper mode can require an authenticated private balance stream; live mode always requires it and repeats the private stream, account, permissions, and public book checks immediately before engine startup. Live mode currently accepts one Binance account per session, caps order size at $25 and the loss stop at $5, and requires the user to type `I ACCEPT REAL ORDERS`. The engine runs the existing latency and risk preflight before placing orders. The service does not retry or auto-resume live trading after a restart. Read [ARBX_README.md](ARBX_README.md) before configuring any exchange key; never enable withdrawals or transfers for a bot key, and never commit API keys.

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
