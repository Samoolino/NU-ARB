# ARBX 2.0 - multi-exchange triangular + cross-exchange arbitrage engine

**Read first.** This code enforces a *modeled pre-trade minimum floor* and *post-trade verification*. It does **not** and cannot assure realized profit: fee-tier differences, latency, partial fills, non-atomic cross-venue orders, and unwind prices can still create losses. On liquid spot markets, most scans show no edge.

## 0. What is in the box

| Layer | File | Job |
|---|---|---|
| Config | `config.py` | env-driven settings, live-mode validation |
| Discovery | `graph.py` | every spot market -> all triangles from stablecoin starts; ranked by liquidity tier, then **lowest total taker fee** (the low-fee "vehicle"); trimmed to your WebSocket budget |
| Streams | `market.py` | one WS watcher per order book, auto-reconnect, local-receive-time staleness, `LatencyGuard` |
| Strategy | `strategy.py` | depth-walking (VWAP) cycle math, IOC limit price per leg, worst-case result |
| **Gate** | `gate.py` | the modeled profit-floor gate + risk kill-switch (every trade passes it) |
| Execution | `execute.py` | Paper / Live (IOC limits, real fills feed next leg, unwind on missed leg) / Cross |
| Orchestration | `worker.py`, `hub.py` | one async worker per exchange, cross-exchange scanner, settle + verify + journal |
| Transfers | `network.py` | cheapest token+chain to *rebalance* inventory between exchanges |
| Ops | `cli.py`, `ui.py`, `selftest.py` | `run`, `probe`, `transfer-plan`, `selftest`, dashboard |

**Deliberate limits:** The registry includes 18 venues, including Binance, Bybit, MEXC, HTX, KuCoin, and Bitfinex. `Bitunix` has no installed ccxt/ccxt.pro adapter and requires a custom integration. "Every token" = every *spot* market the exchange lists, capped by `BOT_MAX_SYMBOLS` streams per venue; subscribing to thousands of books adds latency without adding edge. On-chain transfers are never in the trading loop; cross-exchange trading uses **pre-funded balances on both sides**.

The registry includes 18 venues and allows selecting up to all 18 for verification/scanning; it does not automatically add credentials or start every venue. Permission probes now cover Binance, Bybit, KuCoin, HTX, MEXC, OKX, and Bitfinex. Only Binance, Bybit, or KuCoin can potentially pass the current strict account-scope checks; per-key evidence, authenticated streams, IOC capabilities, and fresh balances still decide eligibility. Cross-exchange scans compare depth breakpoints and available inventory, then rank by the largest modeled dollar floor, with expected return and capital utilization as tie-breakers. A modeled floor is not a promise of realized profit.

## 1. The no-loss gate, precisely

For every trade, before any order is sent:
1. Books fresh (`max_book_age_ms`, local monotonic clock) and REST RTT p95 healthy.
2. Depth-walked expected edge >= `min_net_bps` after **per-market taker fees on every leg**.
3. Each leg gets an **IOC limit price** = marginal book price +/- `limit_tol_bps`.
4. **Modeled floor** = every leg filled *exactly at its IOC limit*, using estimated fees. It must be >= `min_worst_bps` and >= `min_profit_usd`; this is not an execution or realized-PnL guarantee.
5. Exchange min amount / min notional, balance/inventory, trades-per-minute cap, global halt flag.

After every trade, realized PnL is compared with the modeled floor; a miss beyond `verify_slack_bps` halts the bot. **Not covered:** partial/missed legs, non-atomic cross-exchange fills, actual fee differences, and market unwind losses.

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
**Windows (native, no WSL):** install Python 3.12 from python.org (tick *Add to PATH*), open PowerShell in the folder, then:
`pip install -r requirements.txt` -> `python run.py selftest`. In IDLE: open `run.py`, press **F5**.

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
- >= 100 paper trades, PnL > 0 **after** `paper_penalty_bps`, no "MODELED FLOOR BREACHED".
- Rejections dominated by `net_edge` (healthy). Many `stale_book`/`latency_degraded` -> host too slow.
- If there are ~0 trades: that is the true answer for those markets; do not loosen the gate to force trades.
- Check the fee tier you configured equals the exchange's real tier (`BOT_DEFAULT_TAKER_BPS`).

### F. Live pilot (cross-venue, guarded)
The live pilot target defaults to **$200 realized net PnL per ignition**. The hard session-loss ceiling is **$3** (a smaller value is allowed); trade size is capped at **$25**. The operator must choose a positive worst-case edge and minimum profit floor. These bounds do not assure profit or cap the loss from a missed fill, exchange outage, or unwind.

1. Live mode requires at least two venues and explicit `BOT_CROSS_LIVE=1`. Every selected venue must pass read-only account, scope, stream, and execution preflight. Permission evidence is available for seven venues; Binance, Bybit, and KuCoin can potentially pass strict scope checks. No account has been verified in this environment.
2. The transfer planner ranks withdrawal/deposit routes and estimated network costs only. No funds are moved automatically. Cross-venue IOC trades require pre-funded quote balance at the buy venue and base-asset inventory at the sell venue; chain bridges are not atomic with exchange orders and are not an execution leg.
3. `python run.py live-preflight` performs current read-only account, balances, permission, and websocket checks; it places no orders or transfers. `python run.py run --headless` repeats the preflight and starts only if all selected venues pass. An opportunity is reevaluated after balance refresh, but it can still disappear and a partial/missed fill can lose money.
4. `python run.py opportunities` prints the latest persisted depth/latency/edge/gate evidence. `trade_journal.sqlite3` holds opportunity records; `trade_journal.csv` and its SQLite companion record executions. A halt requires an operator review before a new ignition.

### G. Windows PowerShell (no WSL required)
The supported runtime here is native Windows with Python 3.12 and the repository's `.venv`; WSL is not used. From `arb_bot`:
```powershell
& ..\.venv\Scripts\python.exe -m pip install -r requirements.txt
& ..\.venv\Scripts\python.exe run.py selftest
```
To access the credential and account-verification UI locally, start from the repository root:
```powershell
Set-Location C:\Users\saME\Downloads\NU-ARB
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1
```
The browser opens at `http://127.0.0.1:8765`. The local host binds to loopback only; it stores encrypted credentials under `%LOCALAPPDATA%\ARBX`, with the encryption key protected for the current Windows user. Its public scanner can compare selected spot markets across the registered public adapters using REST order-book snapshots, a shared USD-equivalent notional, assumed taker-fee BPS, and a minimum estimated net-edge threshold. USDT, USDC, and DAI are assumed to equal $1 for these estimates. This scanner is read-only and does not use account balances, streams, or place orders; a positive estimate is not assured profit. Live orders are disabled by default. The optional `-EnableLive` switch requires an additional terminal confirmation before enabling live-order requests in the browser; saved venue permissions and fresh preflight checks still apply.

For an interactive local run, start from the repository root (the folder containing `arb_bot`) and launch the root-level helper. It accepts credentials through masked prompts, retains them only in the current PowerShell process while the bot runs, runs the offline self-test and read-only account/balance/scope/WebSocket preflight, and asks for an explicit final confirmation before starting live orders. It supports HMAC credentials for Binance, Bybit, and KuCoin; KuCoin also requires its API passphrase. It does not write credentials to disk or send them to chat.
```powershell
Set-Location C:\Users\saME\Downloads\NU-ARB
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-live.ps1
```
The helper proceeds only if every selected venue reports `liveEligible=true`; the read-only preflight itself never places orders. It defaults to a $5 per-engagement cap (maximum $25), the existing $3 session-loss halt, and the $200 realized-profit stop. Those controls do not guarantee or strictly cap realized loss. Keep the process attached in the terminal; stopping it does not reverse exchange holdings. Docker and WSL are not application requirements.

### H. Linux/VPS live deployment
1. `export BOT_MODE=live BOT_TRADE_SIZE_USD=10 BOT_MAX_LOSS_USD=3 BOT_TARGET_PROFIT_USD=200 BOT_CROSS=1 BOT_CROSS_LIVE=1`.
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
3. After the first 20 live trades compare `trade_journal.csv` with exchange trade history. Scale size only after verifying fees, fills, and unwind outcomes; the modeled floor is not guaranteed.

### I. If it halts
| Log line | Meaning | Action |
|---|---|---|
| `LEG FAILURE ... unwind attempted` | a later leg missed | open the exchange, confirm holdings, flatten manually, review latency |
| `MODELED FLOOR BREACHED` | realized PnL missed the model floor | check fee tier / fee token / tick sizes before restarting |
| `max loss reached` | risk limit | stop; review journal; raise limits only with evidence |
| `disabled: median RTT ...` | host too far | move host region (step A) |
| `not supported by ccxt.pro` | bad exchange id | use an id from `ccxt.pro.exchanges` |

## 4. Tuning table

| Env var | Default | Effect |
|---|---|---|
| `BOT_MIN_NET_BPS` | 3 | expected edge after fees |
| `BOT_MIN_WORST_BPS` | 0.5 | modeled floor in basis points (not a realized-profit guarantee) |
| `BOT_LIMIT_TOL_BPS` | 1 | IOC price tolerance: bigger = more fills, thinner floor |
| `BOT_MAX_RTT_MS` | 80 | live refuses above this median RTT |
| `BOT_MAX_SYMBOLS` | 120 | order-book streams per exchange |
| `BOT_MAX_LOSS_USD` | 5 | cumulative-loss kill switch |
| `BOT_FEE_DISCOUNT_PCT` | 0 | fee-token discount |
| `BOT_CROSS`, `BOT_CROSS_LIVE` | 1, 0 | cross-exchange scan / live orders |

## 5. Pre-funded cross-exchange and rebalancing
Cross trades buy on A and sell on B simultaneously (IOC both sides). Balances drift one way over time. Rebalance **manually** (bot never withdraws) using the cheapest vehicle from `transfer-plan`: stablecoins on the cheapest network (often TRC-20/other low-fee chains) when a flat fee dominates; a coin like TRX/XRP/XLM only if its fee + 2 conversion legs beats that. `rebalance_haircut_bps` (2 bps default in `config.py`) charges that amortized cost against every cross trade.
