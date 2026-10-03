# NU-ARB

ARBX is a Python spot-arbitrage bot for triangular and cross-exchange strategies. The application source is in [`arb_bot/`](arb_bot/), and the operating guide is in [`ARBX_README.md`](ARBX_README.md).

## Vercel web dashboard (read-only)

The repository root includes a Vercel dashboard for public, read-only spot order-book scans. Choose two venues, a USDT-quoted symbol, a quote-currency notional, and estimated taker fees. The serverless API loads market metadata through CCXT, requests each selected order book, walks the visible depth, and estimates both buy/sell directions after the entered fee rates.

The venue picker is configured for Binance, Bybit, OKX, KuCoin, Gate.io, MEXC, HTX, LBank, Bitget, Kraken, Coinbase Exchange, Bitfinex, Bitstamp, Gemini, Crypto.com Exchange, CoinEx, BingX, and WhiteBIT. Actual market availability depends on whether both selected exchanges list the requested spot pair and whether their public API is reachable from the deployed region. The dashboard shows per-venue errors and does not claim unavailable adapters or markets are live.

To deploy, import this GitHub repository in Vercel and set the project root to the repository root. Vercel builds the static `index.html` and the Node.js function at `/api/scan`; no build command or environment variables are required. The app is also runnable locally with Vercel CLI using `vercel dev`.

This dashboard is intentionally **read-only**. It does not collect API keys, access private balances, run persistent WebSocket subscriptions, share state with the Python engine, store a durable journal, or place orders. Fees are estimates, and a positive estimate is not a guaranteed or executable profit. Keep the existing long-running WebSocket scanner and any explicitly authorized trading on a persistent host as described in [ARBX_README.md](ARBX_README.md).

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

