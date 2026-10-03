# NU-ARB

ARBX is a Python spot-arbitrage bot for triangular and cross-exchange strategies. The application source is in [`arb_bot/`](arb_bot/), and the operating guide is in [`ARBX_README.md`](ARBX_README.md).

## Vercel web dashboard (read-only)

The repository root now includes a deployable Vercel dashboard for read-only Binance and Bybit spot market scans. It requests public REST order-book snapshots, walks the available depth for the chosen USDT notional, applies the taker-fee basis points entered in the page, and shows the estimated net result for both buy/sell directions.

To deploy, import this GitHub repository in Vercel and keep the project root set to the repository root. The project uses a static `index.html` and a Node.js serverless function at `/api/scan`; no build command or environment variables are required. The app is also runnable locally with Vercel CLI using `vercel dev`.

This web dashboard is intentionally **read-only**. It does not collect API keys, access private balances, run persistent WebSocket subscriptions, share state with the Python engine, or place orders. A positive estimate is not an executable-profit guarantee. Vercel Functions are request/response handlers; keep the existing long-running WebSocket scanner and any explicitly authorized trading on a suitable persistent host as described in [ARBX_README.md](ARBX_README.md).

Run the dashboard's dependency-free unit tests with Node.js 20 or later:

```sh
npm test
```

## Windows setup

From the repository root, create and activate a virtual environment, install the dependencies, and run the offline self-test:

```powershell
py -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install -r arb_bot\\requirements.txt
Set-Location arb_bot
python run.py selftest
```

To run with live market data and virtual funds, stay in `arb_bot` and run:

```powershell
$env:BOT_MODE = "paper"
python run.py --headless
```

The bot defaults to paper mode. Live mode can place real exchange orders; it requires exchange credentials, and cross-exchange live orders require the separate `BOT_CROSS_LIVE=1` opt-in. Arbitrage is risky and profits are not guaranteed. Read the full [operations guide](ARBX_README.md) before configuring exchanges. Never commit API keys.
