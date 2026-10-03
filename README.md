# NU-ARB

ARBX is a Python spot-arbitrage bot for triangular and cross-exchange strategies. The application source is in [`arb_bot/`](arb_bot/), and the operating guide is in [`ARBX_README.md`](ARBX_README.md).

## Vercel web dashboard

The repository root includes a Vercel dashboard for public, read-only spot order-book scans. Choose two venues, a USDT-quoted symbol, a quote-currency notional, and estimated taker fees. The serverless API loads market metadata through CCXT, requests each selected order book, walks the visible depth, and estimates both buy/sell directions after the entered fee rates.

The venue picker is configured for Binance, Bybit, OKX, KuCoin, Gate.io, MEXC, HTX, LBank, Bitget, Kraken, Coinbase Exchange, Bitfinex, Bitstamp, Gemini, Crypto.com Exchange, CoinEx, BingX, and WhiteBIT. Actual market availability depends on whether both selected exchanges list the requested spot pair and whether their public API is reachable from the deployed region. The dashboard shows per-venue errors and does not claim unavailable adapters or markets are live.

To deploy the public scanner, import this GitHub repository in Vercel and set the project root to the repository root. Vercel serves `index.html` and the Node.js function at `/api/scan`; no build command or environment variables are required. The app is also runnable locally with Vercel CLI using `vercel dev`.

The account panel can create application accounts and verify an exchange API key against that exchange's authenticated REST balance endpoint plus a real public WebSocket order-book message. API secrets are encrypted with AES-GCM in the control service's persistent SQLite database; passwords are PBKDF2 hashed. The interface reports the checks that actually succeeded. It does not enable authenticated opportunity scanning, private WebSocket health checks, or order placement. The server deliberately reports `scannerEligible: false`, `liveEligible: false`, and `executionEnabled: false` because it does not yet prove private-stream health, trade permissions, or withdrawal restrictions. Fees remain estimates, and a positive public-market estimate is not guaranteed executable profit.

### Enable the account panel

Vercel does not provide persistent storage for the control database. Run the Python control API on a separate HTTPS container host with a persistent disk, then configure these Vercel project environment variables:

- `ARBX_CONTROL_API_URL`: the HTTPS origin of the control API, such as `https://arb-control.example.com`.
- `ENGINE_PROXY_TOKEN`: a long random service-to-service token. Set the same value on Vercel and the control host.

On the control host, build and run `Dockerfile.control`, mount persistent storage at `/data`, and configure:

- `ENGINE_PROXY_TOKEN`: the same secret as on Vercel.
- `CREDENTIAL_ENCRYPTION_KEY`: a stable 32-byte key encoded as 64 hex characters. Generate one with `python -c "import secrets; print(secrets.token_hex(32))"` and store it in the host's secret manager. Keep a protected backup; changing or losing this key makes saved exchange credentials unreadable.
- `ARBX_APP_DB=/data/arbx_app.sqlite3`.
- `APP_SECURE_COOKIE=1`.

The container runs Uvicorn on port 8000 and must be reachable only through HTTPS. Restrict direct ingress to the Vercel proxy where the hosting provider supports it. Use one service instance with a durable volume: container-local files will lose accounts, sessions, and saved credentials when the instance is replaced, and SQLite is not configured for multi-instance shared access. The control API has no trading routes and its health response explicitly says execution is disabled.

The existing Python engine remains a separate process. Its realized-profit target and durable execution ledger use `BOT_TARGET_PROFIT_USD` and persistent `BOT_JOURNAL_PATH` storage on the engine host; see [the target and journal guide](ARBX_README.md#global-realized-profit-target-and-durable-journal). The account control service does not yet launch or control that engine. Read the [operations guide](ARBX_README.md) before configuring any exchange access. Never commit API keys.

The Python engine now supports a global realized-profit target with a durable SQLite execution ledger, separate from this Vercel dashboard. Configure `BOT_TARGET_PROFIT_USD` and persistent `BOT_JOURNAL_PATH` storage on the engine host; see [the target and journal guide](ARBX_README.md#global-realized-profit-target-and-durable-journal). The dashboard and engine do not yet share authenticated state or controls.

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

