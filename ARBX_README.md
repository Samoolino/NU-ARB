# ARBX 2.0 - multi-exchange triangular + cross-exchange arbitrage engine

**Read first.** This code enforces a modeled pre-trade minimum floor and post-trade verification. It does **not** and cannot assure realized profit: fee-tier differences, latency, partial fills, non-atomic cross-venue orders, and unwind prices can still create losses. On liquid spot markets, most scans show no edge.

## 0. What is in the box

| Layer | File | Job |
|---|---|---|
| Config | `config.py` | env-driven settings, live-mode validation and $3 starter-capital allocation |
| Discovery | `graph.py` | every spot market -> all triangles from stablecoin starts; ranked by liquidity tier, then lowest total taker fee; trimmed to your WebSocket budget |
| Streams | `market.py` | one WS watcher per order book, auto-reconnect, local-receive-time staleness, `LatencyGuard` |
| Strategy | `strategy.py` | depth-walking (VWAP) cycle math, IOC limit price per leg, worst-case result |
| **Gate** | `gate.py` | modeled profit-floor gate + execution-safety circuit breaker |
| Execution | `execute.py` | Paper / Live (IOC limits, real fills feed next leg, unwind on missed leg) / Cross |
| Orchestration | `worker.py`, `hub.py` | one async worker per exchange, cross-exchange scanner, settle + verify + journal |
| Transfers | `network.py` | cheapest token+chain to rebalance inventory between exchanges |
| Ops | `cli.py`, `ui.py`, `selftest.py` | `run`, `probe`, `transfer-plan`, `selftest`, dashboard |

**Deliberate limits:** The registry includes 18 venues. On-chain transfers are never in the trading loop; cross-exchange trading uses pre-funded balances on both sides.

## 1. The modeled-profit gate

For every trade, before any order is sent:
1. Books fresh (`max_book_age_ms`, local monotonic clock) and REST RTT healthy.
2. Depth-walked expected edge >= `min_net_bps` after per-market taker fees on every leg.
3. Each leg gets an IOC limit price = marginal book price +/- `limit_tol_bps`.
4. Modeled floor = every leg filled exactly at its IOC limit, using estimated fees. It must be >= `min_worst_bps` and >= `min_profit_usd`; this is not an execution or realized-PnL guarantee.
5. Exchange min amount / min notional, balance/inventory, trades-per-minute cap, and global halt state all pass.

After every trade, realized PnL is compared with the modeled floor; a miss beyond `verify_slack_bps` halts the bot. Partial/missed legs, non-atomic cross-exchange fills, actual fee differences, and market unwind losses remain possible.

## 2. Live capital condition: $3 starter capital

The live pilot no longer uses the previous `$25 maximum order` plus `$3 session-loss halt` condition.

The live launcher initializes:

```bash
BOT_MODE=live
BOT_START_CAPITAL_USD=3
BOT_TRADE_SIZE_USD=3
BOT_CROSS=1
BOT_CROSS_LIVE=1
```

There is **no separate session-loss ceiling and no fixed realized-profit target** in the starter-capital launcher. The $3 allocation is the starting capital/notional budget for the pilot. Actual execution remains subject to verified exchange inventory, exchange minimums, modeled net-profit floors, latency, liquidity, rate limits, and the consecutive-failure circuit breaker.

This is intentionally a micro-capital execution test, not a statement that $3 can guarantee profit or eliminate market/execution loss.

## 3. Speed optimizers

- Fast event loop when installed.
- Incremental evaluation: a book delta re-scores only cycles containing that symbol.
- Coalesced wake-ups; no awaits inside evaluation.
- One worker per exchange.
- Persistent WS + HTTP sessions; authenticated connection warmed at startup (live).
- Precomputed fees, minimums, precision.
- IOC limit orders.
- Latency gate: refuses live start if median RTT is above `max_rtt_ms`; pauses trading if p95 is above `pause_rtt_ms`.

## 4. Procedure

### A. Host
Use a VPS close to the relevant exchange API region. Measure RTT and keep live hosts below the configured latency gate.

### B. Server setup
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv unzip chrony
sudo systemctl enable --now chrony
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py selftest
```

### C. Exchange API keys
1. Create a dedicated sub-account funded only with the pilot allocation you can afford to lose.
2. API permissions: Read + Spot Trade ONLY. Withdrawals OFF.
3. Whitelist the VPS IP.
4. Pre-fund the required stablecoin/base inventory on every cross-exchange venue.

### D. Paper mode
Use paper trading with real market data first. Review `trade_journal.csv` and confirm that the modeled floor, fees, slippage assumptions and exchange minimums behave as expected.

### E. Live starter-capital mode
The PowerShell launcher now uses the fixed $3 starter allocation and does not ask for a configurable per-trade cap or session-loss limit:

```powershell
.\start-live.ps1
```

It still requires multiple credentialed venues, offline self-test, live permission preflight, fresh balances, and explicit `START LIVE` confirmation before orders can be sent.

### F. If it halts
| Log line | Meaning | Action |
|---|---|---|
| `LEG FAILURE ... unwind attempted` | a later leg missed | confirm holdings and review the unwind |
| `MODELED FLOOR BREACHED` | realized PnL missed the model floor | check fee tier, fee token, tick sizes and liquidity |
| `too many consecutive failed/unfilled cycles` | execution circuit breaker | stop and review venue health/order state |
| `disabled: median RTT ...` | host too far | move host region |
| `not supported by ccxt.pro` | bad exchange id | use a supported adapter |

## 5. Tuning table

| Env var | Default | Effect |
|---|---:|---|
| `BOT_START_CAPITAL_USD` | 3 | live starter-capital allocation |
| `BOT_TRADE_SIZE_USD` | 3 | default live engagement notional; live cannot exceed starter capital |
| `BOT_MIN_NET_BPS` | 3 | expected edge after fees |
| `BOT_MIN_WORST_BPS` | 0.5 | modeled worst-case floor |
| `BOT_LIMIT_TOL_BPS` | 1 | IOC price tolerance |
| `BOT_MAX_RTT_MS` | 80 | live refuses above this median RTT |
| `BOT_MAX_SYMBOLS` | 120 | order-book streams per exchange |
| `BOT_CROSS`, `BOT_CROSS_LIVE` | 1, 0 | cross-exchange scan / live orders |

`BOT_MAX_LOSS_USD` is retained only for compatibility with older control-plane payloads; the live risk gate no longer uses a session-loss ceiling.

## 6. Pre-funded cross-exchange and rebalancing

Cross trades buy on A and sell on B using pre-funded inventory. Balances drift over time. Rebalance manually using `transfer-plan`; the trading loop never performs withdrawals.
