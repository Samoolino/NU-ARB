# Pre-live hardening baseline

**Status:** NOT CERTIFIED FOR LIVE EXECUTION  
**Purpose:** evidence-led implementation gate for exchange connectivity, latency, PostgreSQL persistence, and containerized paper staging.

## Evidence boundary

This baseline is derived from repository source inspection. It is not a runtime test report. No exchange REST/WebSocket probes, account permission checks, order tests, Docker builds, PostgreSQL integration tests, or full test suites were executed as part of preparing this document. Do not interpret documented adapter support as verified connectivity.

## Source findings

- `Dockerfile.control` sets `ARBX_APP_DB=/data/arbx_app.sqlite3`; `arb_bot/arbx/web_api.py` uses `sqlite3.connect`. PostgreSQL is not currently the active application persistence implementation.
- `arb_bot/arbx/journal.py` also uses SQLite. A complete migration must cover both control-plane data and execution/journal data, not just change an environment variable.
- `arb_bot/arbx/market.py` has a REST round-trip latency sampler and a rolling p95 guard. It also records local receive time for order-book freshness. These mechanisms do not constitute measurements from a particular host or exchange.
- `arb_bot/arbx/worker.py` contains runtime exchange startup checks, including private-stream requirements and permission inspection in live mode. These checks have not been executed in this audit.
- `README.md` contains legacy live limits ($25 order cap, $5 loss stop and a required profit target) that conflict with current `arb_bot/arbx/config.py` defaults and live normalization ($3 starter/trade size, profit compounding, and halt on any realized loss). The user-facing docs and actual control/API path need one authoritative policy and tests.
- `arb_bot/README.md` describes 18 venues while `EXCHANGE_SUPPORT.md` / hybrid documentation describe a 20-venue catalog. Reconcile catalog counts and live eligibility statements against the registry and runtime evidence.
- No Compose file was confirmed in the source files inspected for this baseline. Confirm against the full repository tree before adding one.

## Implementation sequence

### Gate 1 — Offline reproducible baseline

1. Run the repository's existing offline self-test and compile checks.
2. Run Python hybrid-engine tests and the root Node scanner test.
3. Capture command, timestamp, commit SHA, exit status, and logs in the CI artifact or audit report.
4. Reconcile documentation against the current source of truth; do not claim an exchange is live-ready from catalog membership alone.

### Gate 2 — Persistence migration

1. Introduce a PostgreSQL repository/data-access layer for control-plane tables and execution journals.
2. Define versioned migrations, transaction boundaries, connection pooling, startup readiness, and backup/restore procedures.
3. Preserve encryption-at-rest semantics for exchange credentials; never log credentials or connection URLs.
4. Add PostgreSQL integration tests for account/session lifecycle, credential storage/retrieval, verification evidence, execution/session journal durability, concurrent requests, and restart recovery.
5. Keep SQLite compatibility only if explicitly required for local development; document it as a separate mode, not as PostgreSQL-backed staging.

### Gate 3 — Docker Compose paper staging

Compose should be added only when the application can actually use PostgreSQL. It should include:
- PostgreSQL with a named data volume, health check, and non-default development credentials supplied through an ignored local env file.
- The control service with `depends_on: condition: service_healthy`, application health checks, non-root runtime, and `ARBX_LIVE_TRADING_ENABLED=0`.
- A paper-engine profile that cannot submit real orders and does not receive production exchange secrets.
- No published database port by default; expose only the control port to the local host.
- Documented migration, seed-free startup, shutdown, and reset commands.

A PostgreSQL container alongside an application still hard-coded to SQLite is not a successful migration and must not be called PostgreSQL staging.

### Gate 4 — Exchange evidence and latency

For each venue, separately record:
- Public REST reachability and endpoint timings (sample count, p50, p95, p99, errors).
- Public WebSocket connect, first-message time, freshness, reconnect and recovery results.
- Authenticated private-stream result and freshness where supported.
- Account permission evidence, withdrawals disabled, IP restrictions, spot-only capability, and balance freshness.
- Host region, measurement timestamp, clock offset, sample count and exact adapter/version.

Use read-only endpoints and test credentials for verification. A probe must not submit orders or transfers. Report each venue as `not-tested`, `pass`, or `fail`; never infer a pass from documentation.

### Gate 5 — Release decision

Live order submission remains disabled until:
- Offline tests pass and PostgreSQL integration/fault-recovery tests pass.
- Container image and Compose health checks pass from a clean environment.
- Risk policy is consistent across config, API, UI, documentation and tests.
- Every venue selected for a session has fresh, venue-specific evidence.
- Idempotency, partial-fill handling, restart recovery, and kill-switch behavior have been tested.
- An authorized operator separately approves the release. No result in this document authorizes real orders.

## Initial acceptance checklist

- [ ] Offline test run captured against a recorded commit SHA
- [ ] PostgreSQL adapter and versioned schema migrations implemented
- [ ] PostgreSQL integration tests pass
- [ ] Compose starts healthy PostgreSQL and control service from a clean checkout
- [ ] Paper mode is the only mode enabled by default
- [ ] Risk policy conflict resolved and regression-tested
- [ ] Venue catalog count and eligibility matrix agree with code
- [ ] Per-venue REST/WebSocket/permission evidence collected without orders or transfers
- [ ] Latency report includes sample sizes and p50/p95/p99, not just a single RTT
- [ ] Live release gate reviewed and explicitly approved

## Current verdict

**NO-GO for live funds.** Repository mechanisms exist for latency and venue preflight, but current source inspection does not prove they pass on any deployment host. PostgreSQL migration, reproducible container staging, policy reconciliation, and runtime evidence remain outstanding.
