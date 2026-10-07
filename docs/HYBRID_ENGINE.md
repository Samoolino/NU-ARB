# Nu-Arb Hybrid Engine

The Python core remains the authority for normalized market data, depth analysis,
profitability, venue eligibility, risk, execution verification and P&L.

Adapters:
- CCXT Pro: native CEX REST/public WS/private WS/order transport.
- Freqtrade: optional external strategy/process bridge.
- Hummingbot: optional external strategy/process bridge.
- Future chain/DEX adapters: same canonical contracts and gates.

External strategy adapters cannot grant live eligibility.

## Venue validation ladder

CATALOGED -> ADAPTER_AVAILABLE -> REST_OK -> PUBLIC_WS_OK -> PRIVATE_WS_OK ->
BALANCE_OK -> PERMISSION_OK -> EXECUTION_OK -> DEPTH_OK -> LIVE_ELIGIBLE

## Depth certification

Each venue/symbol is checked for fresh bid/ask, receive age, sequence/timestamp when
available, configurable depth, visible quote depth, spread sanity, spot metadata and
order capabilities. Arbitrage calculations walk visible depth rather than trusting
top-of-book prices.

## Strategy base

profit-compounding DCA capital deployment;
cross-exchange spot arbitrage;
intra-exchange triangular arbitrage;
multi-exchange triangular routing;
stablecoin arbitrage;
spot execution/routing.

DCA is capital allocation, not averaging down a losing position. A realized live loss
remains a hard circuit breaker under the current $3 starter policy.

## Twenty-venue candidate set

Binance, Bybit, OKX, KuCoin, Gate.io, Bitget, Kraken, Coinbase, MEXC, HTX,
Bitfinex, Crypto.com, CoinEx, Bitstamp, Gemini, BingX, LBank, WhiteBIT,
BitMart, Upbit.

Catalog membership is not live approval. Runtime evidence decides eligibility.

## Production progression

1. audit: current self-tests and latency probe.
2. scaffold: compile/import contract.
3. validate: read-only venue/account/depth checks.
4. paper: multi-venue live-book paper execution.
5. live-preflight: authenticated eligibility only; zero orders.
6. live: operator-enabled execution after fresh per-venue evidence.

A venue can remain ineligible because its adapter, permissions, private stream,
execution semantics or depth is not currently verifiable.


## Adapter verification contract

Each configured venue is certified independently. The CCXT Pro adapter reports fresh evidence for:

1. **REST** — market load plus exchange server-time request when supported, including measured REST RTT.
2. **Public WebSocket** — live order-book event with bid/ask data, sequence/timestamp when supplied by the venue.
3. **Private WebSocket** — authenticated balance stream with a unified free/used/total snapshot. If `watchBalance` is unavailable, private WS remains unverified; no live approval is inferred.
4. **Authenticated balance** — REST balance snapshot with free/used/total maps.
5. **Permissions** — delegated to `arbx.permissions.inspect_permissions`; an adapter capability flag alone never proves key scopes.
6. **Execution** — spot market, create/fetch order, public book support, and IOC limit support are required for live certification.
7. **Depth** — normalized live order book is walked and checked for freshness, spread and visible quote liquidity for the configured notional.

The resulting evidence is a per-venue certification record. A venue is **LIVE_ELIGIBLE only when every required live check passes**. A failed or unavailable check is retained as a reason and the venue remains disabled.

### Venue-by-venue certification

The read-only PowerShell stage is:

`.scriptsestructure-hybrid.ps1 -Stage venue-verify -Venues binance,bybit,okx -Symbol "BTC/USDT" -Notional 3`

The script tests one venue at a time. It does not submit orders or transfers. This is deliberately separate from production live startup, which still requires the normal multi-venue cross-arbitrage gates.

A single-venue validation uses `BOT_HYBRID_VALIDATION_ONLY=1` only to permit isolated certification. It does **not** relax the production requirement for at least two live venues and explicit cross-live enablement.

### Evidence states

`CATALOGED -> ADAPTER_AVAILABLE -> REST_OK -> PUBLIC_WS_OK -> PRIVATE_WS_OK -> BALANCE_OK -> PERMISSION_OK -> EXECUTION_OK -> DEPTH_OK -> LIVE_ELIGIBLE`

A venue may be cataloged while remaining ineligible. The engine never treats the 20-venue catalog as proof of connectivity, permission, liquidity, or trading readiness.


## Live execution enforcement

The hybrid certification layer is enforced twice in live mode:

1. `hybrid-validate` provides explicit, read-only venue certification.
2. `ExchangeWorker.prepare()` re-certifies the actual venue immediately before constructing the live executor.

A stale or manually asserted certification therefore cannot authorize live trading.

All real order submissions pass through `arbx.execution_gate.ExecutionGate` at
the engine startup boundary and again immediately before each CCXT order call.
Paper mode is always denied by this gate. A live order requires both
`BOT_MODE=live` and `BOT_ALLOW_ORDERS=1`; the control service additionally
requires `ARBX_LIVE_TRADING_ENABLED=1` and the explicit user confirmation.
Legacy aliases such as `BOT_ALLOW_ORDER_SUBMISSION` and
`BOT_ALLOW_CROSS_ORDERS` never grant authority and can only veto when configured
to a value other than `1`. Any other configured `BOT_ALLOW_*` switch is also a
veto unless it is exactly `1`; an absent switch is never an enable.
Cross-exchange submissions additionally require `cfg.cross_live` and
`BOT_CROSS_LIVE=1`. The interactive terminal launcher forces
`BOT_ALLOW_ORDERS=0` through self-test and read-only preflight, sets it to `1`
only after the final `START LIVE` confirmation, and restores the previous
process value when it exits. The example deployment config keeps paper mode and
all order switches at `0`.

Switch approval alone cannot mint an execution permit. The production worker or
hub must also provide explicit true evidence for `venue.liveEligible`, execution
capabilities, the risk decision, available capital/inventory, and route
capabilities. Missing or unknown evidence is rejected. Required authorization
is checked again at every normal-order and emergency-unwind submission boundary.

The live triangular executor, cross executor, and CCXT Pro adapter wrapper are
all guarded; the hybrid adapter wrapper is unavailable for submission unless a
central gate is explicitly injected. Triangular emergency unwind orders require
the same in-process permit minted for the already-authorized execution and are
limited to its reverse-leg recovery path. They do not authorize a new
opportunity. A disabled gate halts live session startup before exchange workers
connect, and repeated authorization happens immediately before submission to
catch changed/expired process switches.

Certification evidence is persisted in the SQLite journal under `venue_certifications`. The record contains venue, symbol, notional, eligibility, failure reasons, and the verification evidence.

## Offline readiness certification

From the repository root, run:

```powershell
.\scripts\certify-live-readiness.ps1
```

The script emits exactly seven named matrices: `VENUE`, `AUTH`, `MARKET DATA`,
`NETWORK`, `EXECUTION`, `RISK`, and `SANDBOX`. Each matrix contains checks with
an explicit status and evidence/limitation text. It checks the checked-in
`BOT_MODE=paper` and explicit `BOT_ALLOW_ORDERS=0` defaults and reports only
safe classifications for those two process values. It never enumerates the
environment or reads credentials.

Static defaults and source markers are not sandbox proof. The script is
read-only and therefore requires a separate, reproducible evidence bundle at
`diagnostics/sandbox-readiness.json`; no such evidence is bundled or presumed
by this repository change. The bundle must bind to the current Git `HEAD` and
SHA-256 of the exact source/test file set, be no more than seven days old, and
include passing exit codes, exact commands, and hashed local logs for:

* from `arb_bot/`: `python run.py selftest` (`SELFTEST PASSED`)
* from repository root: `python tests/test_execution_gate.py`
* from repository root: `python -m pytest -q tests/test_live_limits.py`
* from repository root: `python -m pytest -q tests/test_hybrid_engine.py`

Every log path must remain within the evidence bundle directory and its bytes
must match the recorded SHA-256. The report includes the exact ordered
`evidenceBinding.sourceFiles` list and digest inputs. Missing, stale, malformed, failed, or
source-mismatched evidence keeps the result at `NOT_READY` (exit code 1).
`SANDBOX_READY` (exit code 0) is possible only when that evidence bundle and
all static safety/order-boundary checks validate. This script does not
generate test evidence, execute application code, open the trading journal or
database, contact a venue, or submit an order. It cannot establish
`LIVE_SCAN_READY` or `LIVE_EXECUTION_READY`; those require separate fresh,
authenticated runtime evidence and normal operator controls.

The evidence JSON uses `schemaVersion: 1`, `status: "PASSED"`, the lowercase
40-character `git rev-parse HEAD` value, the lowercase `sourceSha256` shown by
the report, ISO-8601 UTC `completedUtc`, and a `checks` object keyed by
`paper_selftest`, `execution_gate_tests`, `live_limits_tests`, and
`hybrid_engine_tests`. Each check records `status: "PASSED"`, `exitCode: 0`,
the exact command and `workingDirectory` above, plus a relative `logPath` and
the log file's lowercase SHA-256 as `logSha256`. The source digest is SHA-256
over each report-listed file, in listed order, as UTF-8 relative path plus LF,
raw file bytes, then LF. Treat this as a reproducibility/provenance bundle;
it is not a cryptographic CI attestation.

This work delivers readiness-script foundations only. It does not complete
the broader NU-ARB mission or its Phases 0–23.

## Capital policy

Live trading starts at exactly $3.00. After a completed profitable trade, the realized profit is added to the next allocation. A realized loss immediately halts new engagements. Unfilled IOC attempts do not count as realized losses, but repeated failures still trigger the consecutive-failure halt.

There is no fixed $25 order ceiling and no retired $3 session-loss ceiling.

## Production PowerShell sequence

```powershell
cd C:\path\to\Nu-Arb
.\scripts\restructure-hybrid.ps1 -Stage audit
.\scripts\restructure-hybrid.ps1 -Stage scaffold
.\scripts\restructure-hybrid.ps1 -Stage validate
.\scripts\restructure-hybrid.ps1 -Stage paper -Symbol "BTC/USDT" -Notional 3
.\scripts\restructure-hybrid.ps1 -Stage venue-verify -Venues binance,bybit,okx -Symbol "BTC/USDT" -Notional 3
.\scripts\restructure-hybrid.ps1 -Stage live-preflight -Symbol "BTC/USDT" -Notional 3
```

The venue-verification stage performs no orders or transfers. Live execution remains blocked unless the actual worker's fresh certification passes.


## Native adapter architecture

The core no longer treats CCXT/CCXT Pro as the domain abstraction. The Nu-Arb-native `VenueAdapter` contract is the authority. CCXT Pro, CCXT REST, native SDKs, Hummingbot and Freqtrade are transport/integration implementations behind that boundary.

### Adapter selection

`BOT_ADAPTER=ccxt_pro` is the default transport.

`BOT_ADAPTER=ccxt` selects the REST fallback and is not sufficient for production live certification because the live requirements include public/private streaming.

`BOT_ADAPTER=native` selects the fail-closed native SDK boundary. A concrete native implementation must exist before it can connect or become live eligible.

A venue-specific override is supported:

`BOT_BINANCE_ADAPTER=native`

The route validator evaluates capabilities and requirements before a route is eligible.

### Canonical flow

`Strategy -> Opportunity -> CEX Route Optimizer -> PnL/Risk -> Route Validator -> Execution Coordinator -> VenueAdapter -> Transport`

The PnL model exposes expected and worst-case net PnL. Fees, slippage, latency, partial-fill reserve, rebalancing and safety reserve are explicit costs.

The execution coordinator has explicit states for partial fills, hedging and reconciliation. It is currently a safety boundary; end-to-end live submission remains behind the existing worker until coordinator reconciliation is wired into the production order path.

See `docs/NATIVE_ADAPTER_ARCHITECTURE.md` for the migration policy.
