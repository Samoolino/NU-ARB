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

## 1. Modeled edge and loss circuit breaker, precisely

For every trade, before any order is sent:
1. Books fresh (`max_book_age_ms`, local monotonic clock) and REST RTT p95 healthy.
2. Depth-walked expected edge >= `min_net_bps` after **per-market taker fees on every leg**.
3. Each leg gets an **IOC limit price** = marginal book price +/- `limit_tol_bps`.
4. **Modeled floor** = every leg filled *exactly at its IOC limit*, using estimated fees. It must be >= `min_worst_bps` and >= `min_profit_usd`; expected and worst-case PnL plus their positive thresholds are pretrade estimates, not an execution or realized-PnL guarantee.
5. Exchange min amount / min notional, balance/inventory, trades-per-minute cap, global halt flag.

The Phase 10 policy module halts on any supplied realized loss and requires
reconciliation for unknown PnL or account/order state. Reconciliation must
establish known realized PnL, matching balances, no open or unknown orders, and
no partial or unknown fills; its policy remains denied until explicitly
restarted. This pure module is not wired into the live order path and does not
change live behavior. The modeled-floor comparison using `verify_slack_bps` is
additional monitoring, not protection from partial/missed legs, non-atomic
cross-exchange fills, actual fee differences, or market unwind losses. A loss
can still occur.

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
For Binance RSA or Ed25519 credentials, set `BOT_BINANCE_AUTH_MODE` to `rsa` or
`ed25519` and provide the matching private-key text in
`BOT_BINANCE_PRIVATE_KEY` instead of `BOT_BINANCE_SECRET`. Keep key material
server-side and out of source control. The control API likewise stores HMAC
credentials as `apiKey` + `secret`, and RSA/Ed25519 credentials as `apiKey` +
`privateKey`; the Binance CCXT transport performs the venue-specific mapping.
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
The current live configuration fixes starter capital and the initial trade allocation at **$3**, compounds only realized positive PnL, and halts new engagements after a realized loss. It does not guarantee profit or cap losses from missed/partial fills, exchange outages, or unwinds.

1. Live mode requires at least two venues and explicit `BOT_CROSS_LIVE=1`. Every selected venue must pass read-only account, scope, stream, and execution preflight. Permission evidence is available for seven venues; Binance, Bybit, and KuCoin can potentially pass strict scope checks. No account has been verified in this environment.
2. The transfer planner ranks withdrawal/deposit routes and estimated network costs only. No funds are moved automatically. Cross-venue IOC trades require pre-funded quote balance at the buy venue and base-asset inventory at the sell venue; chain bridges are not atomic with exchange orders and are not an execution leg.
3. `python run.py live-preflight` performs current read-only account, balances, permission, and websocket checks; it places no orders or transfers. `python run.py run --headless` repeats the preflight and starts only if all selected venues pass. An opportunity is reevaluated after balance refresh, but it can still disappear and a partial/missed fill can lose money.
4. `python run.py opportunities` prints the latest persisted depth/latency/edge/gate evidence. `trade_journal.sqlite3` holds opportunity and execution records; when the Phase 10 policy/store interface is used, it also persists risk-policy state and sanitized incidents. Phase 10 exposes reconciliation/restart through a pure policy interface, not an operator UI, and is not integrated into live execution. Profit and no-loss outcomes are not guaranteed.

Live submission is separately fail-closed: both `BOT_MODE=live` and
`BOT_ALLOW_ORDERS=1` are required. Cross-exchange orders also require the
separate `BOT_CROSS_LIVE=1` selection. Legacy aliases such as
`BOT_ALLOW_ORDER_SUBMISSION` and `BOT_ALLOW_CROSS_ORDERS` never grant authority;
if present and not exactly `1`, they only veto it. These settings default to
paper/`0` in `.env.control.example`. The interactive launcher forces
`BOT_ALLOW_ORDERS=0` through self-test and read-only preflight, enables it only
after the final `START LIVE` confirmation, and restores its original value on
exit.

### G. Windows PowerShell (no WSL required)
The supported runtime here is native Windows with Python 3.12 and the repository's `.venv`; WSL is not used. Administrator privileges are normally unnecessary for an isolated virtual environment. Open PowerShell in the repository root (elevate only if your machine policy requires it), then install/verify dependencies:
```powershell
Set-Location 'C:\Users\saME\NU-ARB'
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r .\arb_bot\requirements.txt
& .\.venv\Scripts\python.exe -m pip check
Set-Location .\arb_bot
& ..\.venv\Scripts\python.exe run.py selftest
```
If `.venv` already exists, omit the `py -3.12 -m venv .venv` line. Do not install
into the system Python.

**Credential incident:** all API keys/secrets previously pasted into chat must
be treated as compromised. Revoke them at the exchanges, rotate them, and use
only fresh keys entered at a local masked prompt. Never set credentials in
commands, source files, screenshots, or chat.

**Read-only prerequisite scans:** first inspect paper feeds and permission evidence:
```powershell
Set-Location 'C:\Users\saME\NU-ARB'
Set-Location .\arb_bot
& ..\.venv\Scripts\python.exe run.py public-feed-check binance mexc kucoin htx
Set-Location ..
.\arb_bot\validate-authenticated-feeds.ps1 -Symbol BTC/USDT -Venues bybit,kucoin,mexc,htx
```
Enter only rotated credentials at the local masked prompts. The authenticated
scanner requires complete per-key permission evidence and valid REST plus
WebSocket books for the same exact spot pair. Binance Ed25519 is supported by
selecting that mode and supplying a local PKCS8 PEM path; the key is validated
without printing its contents. MEXC and HTX can report valid
permission-probe responses, but the project cannot prove all restrictions
required for live mode, so they are not live candidates. KuCoin additionally
requires its API passphrase. OKX also requires its passphrase. These checks are
read-only and are not balance, chain-route, or trade certification. The
supported permission-probed venues are Binance, Bybit, KuCoin, MEXC, HTX, OKX,
and Bitfinex.

**Guarded live launcher:** this is an operator-controlled real-order path, not
a prompt that should be run with the chat-exposed keys. Only use it after
revoking/rotating those keys, meeting the documented paper-trading criteria,
reviewing the risks, and verifying account permissions and balances yourself:
```powershell
Set-Location 'C:\Users\saME\NU-ARB'
.\start-live.ps1
```
The launcher accepts at least two distinct venues from Binance, Bybit, and
KuCoin; MEXC and HTX are deliberately excluded by the current live permission
policy. It prompts for credentials locally, runs the offline self-test and
authenticated read-only live preflight, and will not turn on order submission
unless every gate passes and you type exactly `START LIVE`. Do not bypass those
gates. The launcher's supported credential prompts are Binance HMAC key/secret
or Ed25519 API key plus a local PKCS8 PEM private-key file, Bybit key/secret,
and KuCoin key/secret/passphrase. Current live allocation starts at $3, and any
realized loss halts further engagements. Cross-venue orders are non-atomic;
losses remain possible and profit is not assured.

The engine does **not** perform an all-chain balance scan. Settlement networks
are catalog metadata only; the transfer planner is read-only and does not prove
deposits/withdrawals are enabled or submit transfers. Account balances are
available only through explicit authenticated account preflight for configured
venues and supported assets.

To verify public REST and live order-book WebSocket feeds at Binance, MEXC,
KuCoin, and HTX, then run the paper engine on those live feeds from the
repository root:
```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\arb_bot\start-paper-feeds.ps1
```
This checks `BTC/USDT` by default (override with `-Symbol ETH/USDT`), uses no
credentials, submits no orders, and stops before the engine starts if any
venue's REST or WebSocket book cannot be verified. The continuing engine uses
live market data but stays in `BOT_MODE=paper`; this is not live execution.

For a persistent read-only audit across the full 20-venue catalog:
```powershell
Set-Location .\arb_bot
& ..\.venv\Scripts\python.exe run.py live-engagement-audit
```
Each run checks public BTC/USDT REST and WebSocket books where listed and saves
a timestamped report plus `diagnostics\live-engagement-audit-latest.json`.
The report separately records venue feed results, strategy catalog entries,
and the canonical settlement-network catalog. Network catalog membership is
not proof of venue chain support or transfer-route availability. This procedure
does not authenticate, inspect private balances/permissions, submit orders or
transfers, or establish live eligibility. It never guarantees that a displayed
price difference is executable or profitable; live engagement remains blocked
until fresh per-account and per-route live preflight succeeds.

To revalidate and persist the four required core venues specifically
(Binance, MEXC, KuCoin, HTX), use:
```powershell
& ..\.venv\Scripts\python.exe run.py required-live-venue-audit
```
This writes timestamped and `required-live-venue-audit-latest.json` reports
under `diagnostics\`, with per-venue adapter constructor, REST/WS results, exact
feed failures, and explicit missing live-evidence blockers. It is public-data
only; even four successful books do not make the account or order route
live-engageable. No price difference assures execution or profit.

For a fresh random active spot pair on every venue in the catalog, testing both
REST and WebSocket order books for the same pair:
```powershell
& ..\.venv\Scripts\python.exe run.py randomized-venue-feed-audit
```
The command records the random seed and sampled pair, checks the configured
CCXT Pro adapter, and persists timestamped and latest JSON reports under
`diagnostics\`. Failures identify the stage and reason. One random pair per
venue is a connectivity smoke test, not full market/route coverage. Each
sample also runs a read-only depth pilot on both books: it requires three
visible levels per side, checks a 25-unit quote-depth threshold, and simulates
an immediate buy/sell walk inside that single venue's book. The simulated
round trip excludes fees and is not an arbitrage or profit claim. The sampled
symbols are spot markets, not blockchain settlement routes. Reports explicitly
keep live engagement blocked because authentication, balances, permissions,
private streams, fees, order constraints, risk, and settlement are not tested.
No orders, withdrawals, or transfers are made.

To persist one fail-closed summary of the latest public, permission, and strict
same-pair feed evidence after running those audits:
```powershell
& ..\.venv\Scripts\python.exe run.py readiness-state
```
This writes timestamped and `diagnostics\live-readiness-state-latest.json`
reports. It accepts only schema/scope-matching read-back-validated reports and
marks evidence older than 15 minutes stale. It does not read credential stores,
balances, live environment switches, or private streams, and therefore never
marks a venue or strategy live eligible. The three current permission-policy
candidates are Binance, Bybit, and KuCoin; at least two fresh, independently
certified venue connections are required for cross-venue live execution.

Only two catalog entries are distinct order execution paths today:
`cross_exchange` (two eligible venues) and `triangular_intra_exchange` (one
eligible venue with a three-spot-market cycle). Both remain fail-closed and
require explicit, separate live opt-ins. `dca_profit_compounding` is a capital
policy, `stablecoin_arbitrage` is a helper calculation without its own route,
`triangular_multi_exchange` has no distinct executor, and `spot` is a market
type rather than an arbitrage strategy. Catalog membership or public feed
connectivity is not strategy or account certification.

For authenticated permission-policy revalidation of all seven permission-probed
venues, first
run the masked local PowerShell helper from the repository root:
```powershell
Set-Location 'C:\Users\saME\NU-ARB'
.\arb_bot\revalidate-permissions.ps1
```
The default set is Binance, Bybit, KuCoin, MEXC, HTX, OKX, and Bitfinex.
Run a subset with `.\arb_bot\revalidate-permissions.ps1 -Venues mexc,htx`.
This local-only command uses authenticated permission endpoints and persists
timestamped plus latest credential-free JSON reports in `diagnostics\`.
Transient network/time-out/rate-limit failures retry with exponential backoff
(30 seconds to 15 minutes by default); Ctrl+C stops the loop and preserves the
latest report. Authentication failures, unsupported endpoints, and incomplete
permission responses stop for operator action. A validated response that lacks
safe spot-only, disabled-transfer, withdrawal-disabled, or IP-allowlist proof
is recorded as policy-blocked rather than retried endlessly. MEXC and HTX
currently cannot prove all live permission requirements and therefore remain
ineligible; permission validation alone never grants live execution. The command
does not query balances, start streams, submit orders, or perform transfers.
The helper restores the process environment on exit. Never place credentials
in source files, reports, or chat; use newly rotated keys entered only in the
masked local prompt.

To strictly validate authenticated permission evidence and both REST and
WebSocket books for the *same exact spot pair* on all selected permission-probed
venues, use:
```powershell
Set-Location 'C:\Users\saME\NU-ARB'
.\arb_bot\validate-authenticated-feeds.ps1 -Symbol BTC/USDT -Venues mexc,htx,kucoin
```
The default exact pair is `BTC/USDT`. One pair is checked across all selected
venues; each venue must first return complete key-permission evidence, then
provide active spot-market metadata and valid, non-crossed, correctly sorted
REST and WebSocket books with positive finite levels. Transient timeouts,
network, exchange-unavailable, and rate-limit errors retry with exponential
backoff (30 seconds to 15 minutes); a permission or pair-policy failure is
recorded and stops retrying. Results persist in timestamped and
`diagnostics\authenticated-feed-validation-latest.json` reports. A pair feed
pass does not imply `liveEligible`: MEXC and HTX can pass permission-evidence
validation while remaining blocked by missing scope/IP/transfer proof. This
scanner does not query balances or place orders/transfers.

To access the credential and account-verification UI locally, start from the repository root:
```powershell
Set-Location C:\Users\saME\Downloads\NU-ARB
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1
```
The browser opens at `http://127.0.0.1:8765`. The local host binds to loopback only; it stores encrypted credentials under `%LOCALAPPDATA%\ARBX`, with the encryption key protected for the current Windows user. Its public scanner can compare selected spot markets across the registered public adapters using REST order-book snapshots, a shared USD-equivalent notional, assumed taker-fee BPS, and a minimum estimated net-edge threshold. USDT, USDC, and DAI are assumed to equal $1 for these estimates. This scanner is read-only and does not use account balances, streams, or place orders; a positive estimate is not assured profit. Live orders are disabled by default. The optional `-EnableLive` switch requires an additional terminal confirmation before enabling live-order requests in the browser; saved venue permissions and fresh preflight checks still apply.

For an interactive local run, start from the repository root (the folder containing `arb_bot`) and launch the root-level helper. It accepts credentials through masked prompts, retains them only in the current PowerShell process while the bot runs, runs the offline self-test and read-only account/balance/scope/WebSocket preflight, and asks for an explicit final confirmation before starting live orders. It supports Binance HMAC or Ed25519 (local PKCS8 PEM file), Bybit HMAC,
and KuCoin HMAC plus API passphrase. It does not write credentials to disk or
send them to chat.
```powershell
Set-Location C:\Users\saME\Downloads\NU-ARB
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-live.ps1
```
The helper proceeds only if every selected venue reports `liveEligible=true`; the read-only preflight itself never places orders. The current policy fixes the initial allocation at $3 and halts after a realized loss; it does not guarantee a profit or a maximum realized loss. Keep the process attached in the terminal; stopping it does not reverse exchange holdings. Docker and WSL are not application requirements.

### H. Linux/VPS live deployment
1. Do not enable live trading by copying generic environment-variable examples. Use only after the live preflight and risk requirements above pass; the supported interactive Windows launcher enforces its own gates. The Linux environment-variable path is operator-managed and has no equivalent masked prompt/final-confirmation wrapper documented here.
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
| `BOT_MAX_LOSS_USD` | 0 | legacy compatibility value; live loss halt is event-based |
| `BOT_FEE_DISCOUNT_PCT` | 0 | fee-token discount |
| `BOT_CROSS`, `BOT_CROSS_LIVE` | 1, 0 | cross-exchange scan / live orders |
| `BOT_ALLOW_ORDERS` | 0 | central live-order authorization; requires `BOT_MODE=live` |

## 5. Pre-funded cross-exchange and rebalancing
Cross trades buy on A and sell on B simultaneously (IOC both sides). Balances drift one way over time. Rebalance **manually** (bot never withdraws) using the cheapest vehicle from `transfer-plan`: stablecoins on the cheapest network (often TRC-20/other low-fee chains) when a flat fee dominates; a coin like TRX/XRP/XLM only if its fee + 2 conversion legs beats that. `rebalance_haircut_bps` (2 bps default in `config.py`) charges that amortized cost against every cross trade.
