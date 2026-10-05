# NU-ARB audit clarity baseline — 2026-10-05

## Audit position

This document records the execution boundary at commit `3c2dc1e2cc322c5ca8e78ee9d468bffb3b175989`.

**Current posture: PAPER / VERIFICATION READY; LIVE NOT APPROVED.**

The repository contains a multi-venue triangular and cross-exchange arbitrage engine, a public read-only scanner, and a Railway/Vercel control plane. The README explicitly separates public scanning, authenticated verification, execution capability, and live eligibility. The engine's profit control is a **modeled floor**, not a guarantee of realized profit.

## 1. What is presently clear

- The core execution architecture is already present: discovery, WebSocket market data, depth/VWAP strategy calculation, profit/risk gate, paper/live execution, cross-exchange orchestration, inventory controls, unwind handling, journal, and self-test.
- Live execution is fail-closed by design and requires explicit operator action plus fresh verification.
- Credentials are intended to remain server-side and encrypted; no credentials belong in Git.
- Scanner eligibility is distinct from live execution eligibility.
- The registry contains 18 venues, but registry presence is not treated as evidence of operational support.
- Permission evidence is intentionally conservative; only venue-specific probes can establish live eligibility.
- Cross-exchange trading is pre-funded; transfers are outside the trading loop.
- The project has dedicated tests for scanner behavior, control API, permissions, live health, live limits, cross-ranking, public scanning, verification, and journal opportunity evidence.

## 2. Audit findings requiring execution clarity

### A. Verification evidence

**Status: defined, implementation-backed, runtime evidence still required.**

A code path can establish `FULLY_VERIFIED`, `scannerEligible`, `executionEligible`, and `liveEligible`, but the repository alone cannot prove that a real account, current exchange permission set, current IP allowlist, current WebSocket stream, and current order path have all succeeded in production.

**Execution requirement:** perform fresh read-only preflight for each intended venue and retain evidence of REST, balance, public WS, private WS, permissions, latency, and execution capability.

### B. Exchange support

**Status: registry broad; live capability narrow.**

18 venues are selectable, but only venues with the required adapter and fresh capability evidence may proceed. Permission-probe coverage is intentionally incomplete for several venues, so they remain live-ineligible until a venue-specific safe-scope probe is implemented and verified.

**Execution requirement:** maintain a venue-by-venue matrix of adapter availability, REST auth, balance, public WS, private WS, order capability, permission evidence, IP restriction evidence, and live eligibility.

### C. Profit model

**Status: modeled and guarded; not a no-loss guarantee.**

The gate requires fresh books, modeled net edge, IOC limits, estimated fees, modeled floor, minimums, inventory, and risk limits. Post-trade verification compares realized PnL with the modeled floor and can halt the engine. This still cannot eliminate partial-fill, fee-tier, latency, non-atomic cross-venue, or unwind risk.

**Execution requirement:** validate configured fee tiers against actual exchange account fees and compare journal outcomes with exchange trade history before increasing size.

### D. Live safety

**Status: structurally fail-closed; operational approval outstanding.**

Live mode remains disabled by default in the documented deployment configuration. The documented pilot limits include small order sizing, a loss stop, explicit cross-live opt-in, and no automatic resume after a halt/restart.

**Execution requirement:** do not enable live mode until infrastructure, secrets, HTTPS, persistent storage, IP allowlisting, venue verification, and paper criteria are independently checked.

### E. Runtime testing

**Status: substantial unit/self-test coverage; environment execution not proven by repository inspection alone.**

The repository contains offline self-tests and multiple Python/Node test suites. The audit must distinguish **tests present** from **tests actually executed against the current checkout**.

**Execution requirement:** run the exact repository test commands in the target environment and record pass/fail output, Python/Node versions, dependency versions, and any skipped tests.

## 3. Execution gates

### Gate 0 — Code integrity
- [ ] Clean checkout at intended commit
- [ ] Python self-test passes
- [ ] Node scanner tests pass
- [ ] Python test suite passes
- [ ] No secrets detected

### Gate 1 — Public market data
- [ ] Intended venues expose real public markets
- [ ] Public order-book messages received
- [ ] Staleness/latency gates behave correctly
- [ ] No fabricated connectivity or balances

### Gate 2 — Authenticated verification
For each intended venue:
- [ ] credentials accepted without exposing secrets
- [ ] authenticated REST succeeds
- [ ] balance evidence succeeds
- [ ] private WS message succeeds
- [ ] public WS message succeeds
- [ ] capability metadata supports intended execution

### Gate 3 — Permission safety
- [ ] spot trading scope is proven where required
- [ ] withdrawals/transfers are disabled
- [ ] IP restriction is proven
- [ ] unsupported/ambiguous permission evidence remains ineligible

### Gate 4 — Paper execution
- [ ] real market data, virtual execution
- [ ] minimum paper sample achieved
- [ ] positive PnL after configured penalty
- [ ] no modeled-floor breaches
- [ ] stale/latency rejections are understood
- [ ] configured fee tier matches actual fee tier

### Gate 5 — Live pilot
**Not approved by this audit document.** Requires explicit operator authorization after Gates 0–4 pass. Start with the smallest configured limits, one controlled session, no automatic restart, and immediate journal/exchange-history reconciliation.

## 4. Audit language standard

Use these terms consistently:

- **adapterAvailable** — installed adapter exists; not proof of connectivity.
- **scannerEligible** — fresh authenticated account and market-data evidence; not proof of trading permission.
- **executionEligible** — requested market and order capabilities are supported; not proof that an account is permitted to trade.
- **liveEligible** — fresh evidence, execution capability, and strict venue-specific permission checks all pass.
- **modeled floor** — pre-trade calculated floor based on assumptions; never call it guaranteed profit.
- **realized PnL** — actual completed execution result after applicable costs.

## 5. Next execution sequence

1. Run code/self-test audit.
2. Run public connectivity/latency probe from the intended host region.
3. Run read-only authenticated preflight for exactly the intended venues.
4. Capture permission and stream evidence.
5. Run paper mode with persistent journal.
6. Reconcile paper behavior and modeled-floor outcomes.
7. Only after all gates pass, request explicit live-pilot authorization.

**Safety boundary:** this audit does not place orders, enable live mode, alter exchange permissions, or weaken any existing risk gate.
