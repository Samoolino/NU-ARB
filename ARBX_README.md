# ARBX 2.0 - multi-exchange triangular + cross-exchange arbitrage engine

**Read first.** This code enforces a *pre-trade worst-case floor* and *post-trade verification* on every order. It does **not** and cannot guarantee profit: fees, latency and missed legs decide the outcome. On liquid spot markets, most scans show no edge. Paper mode will tell you honestly how often one appears.

## 0. What is in the box

| Layer | File | Job |
|---|---|---|
| Config | `config.py` | env-driven settings, live-mode validation |
| Discovery | `graph.py` | every spot market -> all triangles from stablecoin starts; ranked by liquidity tier, then **lowest total taker fee** (the low-fee "vehicle"); trimmed to your WebSocket budget |
| Streams | `market.py` | one WS watcher per order book, auto-reconnect, local-receive-time staleness, `LatencyGuard` |
| Strategy | `strategy.py` | depth-walking (VWAP) cycle math, IOC limit price per leg, worst-case result |
| **Gate** | `gate.py` | the Profit/No-Loss gate + risk kill-switch (every trade passes it) |
| Execution | `execute.py` | Paper / Live (IOC limits, real fills feed next leg, unwind on missed leg) / Cross |
| Orchestration | `worker.py`, `hub.py` | one async worker per exchange, cross-exchange scanner, settle + verify + journal |
| Transfers | `network.py` | cheapest token+chain to *rebalance* inventory between exchanges |
| Ops | `cli.py`, `ui.py`, `selftest.py` | `run`, `probe`, `transfer-plan`, `selftest`, dashboard |

**Deliberate limits:** `Bitunix` is not in ccxt/ccxt.pro (checked, v4.5.84), so it is not supported here; it needs a custom adapter. Supported examples: binance, bybit, okx, kucoin, gate, mexc, bitget, htx. "Every token" = every *spot* market the exchange lists, capped by `BOT_MAX_SYMBOLS` streams per exchange (ranked by volume); subscribing to thousands of books adds latency without adding edge. On-chain transfers are never in the trading loop (minutes, not milliseconds); cross-exchange trading uses **pre-funded balances on both sides**.

## 1. The no-loss gate, precisely

For every trade, before any order is sent:
1. Books fresh (`max_book_age_ms`, local monotonic clock) and REST RTT p95 healthy.
2. Depth-walked expected edge >= `min_net_bps` after **per-market taker fees on every leg**.
3. Each leg gets an **IOC limit price** = marginal book price +/- `limit_tol_bps`.
4. **Worst case** = every leg filled *exactly at its limit*, fees included. Must be >= `min_worst_bps` and >= `min_profit_usd`. Any fill within limits is at least this good.
5. Exchange min amount / min notional, balance/inventory, trades-per-minute cap, global halt flag.

After every trade: realized PnL must be >= guaranteed floor - `verify_slack_bps`; otherwise the bot **halts** ("ASSURANCE VIOLATED": wrong fee tier, rounding, etc.).
**Not covered:** a later leg missing after an earlier leg filled. The bot then market-unwinds the executed legs (bounded loss), **halts**, and tells you to check the account.

## 2. Speed optimizers (what is actually applied)

- Fast event loop (`uvloop` Linux / `winloop` Windows) when installed.
- Incremental evaluation: a book delta re-scores **only cycles containing that symbol**; early exit before building limits.
- Coalesced wake-ups (many deltas -> one evaluation pass); no awaits inside evaluation (consistent snapshot).
- One worker per exchange: a slow order on exchange A never blocks streams on B.
- Persistent WS + HTTP sessions; authenticated connection warmed at startup (live).
- Precomputed fees, minimums, precision; GC frozen + relaxed thresholds; high process priority.
- IOC limit orders (price-protected, no resting orders to cancel).
- Latency gate: refuses live start if median RTT > `max_rtt_ms` (80 ms); pauses trading if p95 > `pause_rtt_ms` (150 ms).
- Not done by code (infrastructure): **co-location**, see step 2.

## 3. Procedure (byte-sized steps)

### A. Decide where to host (latency is the strategy)
1. A home/office connection far from the exchange (typically 150-300 ms from West Africa to Asian/EU matching engines) **cannot** compete. Use it for paper mode only.
2. Find the region of each exchange's API servers (exchange docs/support; many are on AWS. Verify, don't assume).
3. Rent a small VPS **in that region** (2 vCPU / 2 GB is enough): AWS EC2, or Vultr / Hetzner / DigitalOcean nearest region. Ubuntu 24.04.
4. Measure from each candidate (step D-2). Keep the host with the lowest p50. Target: **< 15 ms excellent, < 40 ms workable, > 80 ms live is blocked**.

### B. Server setup (Ubuntu VPS)
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv unzip chrony
sudo systemctl enable --now chrony          # accurate clock (signed requests)
unzip arb_bot.zip -d ~/arb_bot && cd ~/arb_bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt             # ccxt + uvloop
python run.py selftest                      # must print SELFTEST PASSED (offline, no keys)
```
**Windows (native, no WSL):** Python 3.12 is supported; use PowerShell and the project's `.venv`, install `requirements.txt`, and run `selftest` before the read-only `live-preflight`. Full live cross-venue execution still requires two venues with authoritative live-permission probes; currently only Binance can qualify, so startup remains fail-closed. See [the Windows pilot setup](arb_bot/README.md#windows-powershell-no-wsl-required).

### C. Exchange API keys (do this on the exchange website)
1. Create a **new sub-account** funded with only what you can afford to lose.
2. API key permissions: **Read + Spot Trade ONLY. Withdrawals OFF.** (This bot never withdraws; a stolen key then cannot drain funds.)
3. **Whitelist your VPS IP** on the key.
4. Optional fee cut: hold the exchange fee token (e.g. BNB on Binance), enable "pay fees with token", then set `BOT_FEE_DISCOUNT_PCT` to the exchange's stated discount.
5. Pre-fund: stablecoin (USDT/USDC) on every exchange; for cross-exchange trading also hold the base coins you will sell.

### D. Configure and probe
Linux (`~/.bashrc` or a `.env` you `source`; never commit it) / Windows (`setx NAME "value"`, then restart the terminal/IDLE):
```bash
export BOT_EXCHANGES="binance,bybit"
export BOT_MODE="paper"
export BOT_TRADE_SIZE_USD="25"
export BOT_BINANCE_KEY="..." BOT_BINANCE_SECRET="..."     # only needed for live
export BOT_BYBIT_KEY="..."   BOT_BYBIT_SECRET="..."
```
1. `python run.py probe`  -> RTT table + verdict per exchange (public endpoints, no keys).
2. Repeat on each candidate VPS region; pick the best; discard the rest.
3. `python run.py transfer-plan` -> cheapest token+network for rebalancing (needs read-only key on most exchanges).

### E. Paper-trade (mandatory, >= 48 h, real market data, virtual money)
```bash
python run.py --headless 2>&1 | tee paper.log     # or dashboard: python run.py
```
Review `trade_journal.csv`. **Go-live criteria - all must hold:**
- >= 100 paper trades, PnL > 0 **after** `paper_penalty_bps`, no "ASSURANCE VIOLATED".
- Rejections dominated by `net_edge` (healthy). Many `stale_book`/`latency_degraded` -> host too slow.
- If there are ~0 trades: that is the true answer for those markets; do not loosen the gate to force trades.
- Check the fee tier you configured equals the exchange's real tier (`BOT_DEFAULT_TAKER_BPS`).

### F. Live (small first)
1. `export BOT_MODE=live BOT_TRADE_SIZE_USD=10 BOT_MAX_LOSS_USD=2` (cross-exchange stays off unless `BOT_CROSS_LIVE=1`).
2. Run headless in a supervisor so it restarts and logs:
```ini
# /etc/systemd/system/arbx.service
[Unit]
Description=ARBX
After=network-online.target
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/arb_bot
EnvironmentFile=/home/ubuntu/arb_bot/.env
ExecStart=/home/ubuntu/arb_bot/.venv/bin/python run.py --headless
Restart=no
[Install]
WantedBy=multi-user.target
```
(`Restart=no` on purpose: a halt means a human must look.) `sudo systemctl enable --now arbx` -> `journalctl -u arbx -f`.
3. After the first 20 live trades compare `trade_journal.csv` with exchange trade history. Scale size **only** if realized ~ guaranteed floor.

### G. If it halts
| Log line | Meaning | Action |
|---|---|---|
| `LEG FAILURE ... unwind attempted` | a later leg missed | open the exchange, confirm holdings, flatten manually, review latency |
| `ASSURANCE VIOLATED` | realized < guaranteed | check fee tier / fee token / tick sizes before restarting |
| `max loss reached` | risk limit | stop; review journal; raise limits only with evidence |
| `disabled: median RTT ...` | host too far | move host region (step A) |
| `not supported by ccxt.pro` | bad exchange id | use an id from `ccxt.pro.exchanges` |

## 4. Tuning table

| Env var | Default | Effect |
|---|---|---|
| `BOT_MIN_NET_BPS` | 3 | expected edge after fees |
| `BOT_MIN_WORST_BPS` | 0.5 | guaranteed floor (raise for safety) |
| `BOT_LIMIT_TOL_BPS` | 1 | IOC price tolerance: bigger = more fills, thinner floor |
| `BOT_MAX_RTT_MS` | 80 | live refuses above this median RTT |
| `BOT_MAX_SYMBOLS` | 120 | order-book streams per exchange |
| `BOT_MAX_LOSS_USD` | 5 | cumulative-loss kill switch |
| `BOT_FEE_DISCOUNT_PCT` | 0 | fee-token discount |
| `BOT_CROSS`, `BOT_CROSS_LIVE` | 1, 0 | cross-exchange scan / live orders |

### Global realized-profit target and durable journal

Set `BOT_TARGET_PROFIT_USD` to a positive number to stop initiating new trades once cumulative realized session PnL reaches the target. The target is global across the strategies and symbols enabled in the session; it is not tied to BTC/USDT or another pair. Paper mode tracks virtual realized PnL from its paper executor. Live mode counts only completed executor results. A target stop also halts the engine, so a person must review the journal before starting a new session.

The engine keeps the existing `trade_journal.csv` for compatibility and writes a durable SQLite ledger beside it as `trade_journal.sqlite3`. Set `BOT_JOURNAL_PATH` to choose the CSV path; the SQLite file uses the same path with a `.sqlite3` suffix. Store both files on persistent storage when the engine host is restarted or redeployed.

To resume the same target session after a restart, set `BOT_SESSION_ID` to the session ID shown in the engine stats. The engine reloads that session's filled net PnL from SQLite and halts immediately if the target was already attained. If no session ID is supplied, a new isolated target session is created at process start.

Example:

```bash
export BOT_TARGET_PROFIT_USD=100
export BOT_JOURNAL_PATH=/var/lib/arbx/trade_journal.csv
python run.py --headless
```

The SQLite ledger records session, strategy/path, requested notional, execution result, status, failure reason, latency, and target progress. It deliberately leaves unavailable per-leg fill, fee, and slippage detail blank rather than substituting estimates. The current Vercel dashboard is still a separate public-data scanner and does not read or control this ledger or engine.

## 5. Pre-funded cross-exchange and rebalancing
Cross trades buy on A and sell on B simultaneously (IOC both sides). Balances drift one way over time. Rebalance **manually** (bot never withdraws) using the cheapest vehicle from `transfer-plan`: stablecoins on the cheapest network (often TRC-20/other low-fee chains) when a flat fee dominates; a coin like TRX/XRP/XLM only if its fee + 2 conversion legs beats that. `rebalance_haircut_bps` (2 bps default in `config.py`) charges that amortized cost against every cross trade.
