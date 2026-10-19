# Next implementation sequence: parity, evidence, and release gates


## 1A. Durable engine-session registry foundation (staged)

Migrations `004_engine_session_registry.sql`, `005_engine_session_phase_constraints.sql`, `006_engine_worker_leases.sql`, and `007_engine_session_owner_updated_index.sql` and `ControlPlaneStore` define a backend-neutral registry for paper/simulation sessions plus an exclusive, expiring worker lease with a monotonically increasing fencing token on worker takeover. The latest-session read uses an owner-filtered, bounded query backed by an index on owner and update time rather than loading a fixed page and choosing in application memory. Migration 005 constrains persisted lifecycle phases to the application's recognized state vocabulary. The registry stores the owning user, lifecycle phase/timestamps, sanitized error code, and metadata, and supports owner-scoped listing and owner-checked updates. The schema intentionally rejects `mode='live'`.

This remains a partial runtime integration, not a production cutover. The PostgreSQL engine-state route reads the authenticated owner's latest durable session and the journal route scopes trades/PnL to that owner's durable session IDs; both remain behind `POSTGRES_CONTROL_API_PARITY_COMPLETE`. A store-injected `_load_user_credentials` helper now decrypts only explicitly requested credentials for the authenticated owner without opening or falling back to SQLite, with a disposable-PostgreSQL integration test. `DurableEngineRuntime` now provides initial lease acquisition, heartbeat/takeover and owner-scoped fenced lifecycle transitions; `ControlPlaneStore.transition_engine_session_with_lease` checks owner, worker, fencing token and lease expiry while updating lifecycle state transactionally. These are tested primitives, not yet a complete worker loop: the FastAPI start/stop routes remain explicitly fail-closed for PostgreSQL, credential loading is not yet connected to an enabled engine lifecycle, and external side effects are not yet guarded by the fencing token. Crash recovery/checkpoint semantics and restart orchestration remain outstanding. Keep global readiness closed until the complete runtime and real-PostgreSQL isolation/recovery tests are closed.

## 1. Persistence cutover boundary

SQLite remains the default. Setting `ARBX_STORAGE_BACKEND=postgres` only constructs a backend object; it does not migrate the control API or `TradeJournal`, prove schema parity, copy data, or switch existing call sites.

Do not enable PostgreSQL for application traffic until all of the following are complete:
- [ ] The API uses the storage contract for every persisted user/session/credential operation.
- [ ] `TradeJournal` uses the storage contract for trades, opportunities, and venue certifications.
- [ ] A PostgreSQL schema migration covers the API's current user, session, and encrypted-credential fields without silently changing identity or encryption semantics.
- [ ] SQLite and PostgreSQL contract tests run the same logical test cases against both backends.
- [ ] Existing SQLite data has a separately reviewed, restartable migration/export/import procedure and reconciliation report.
- [ ] Rollback behavior is documented and tested; backend selection alone is not rollback or migration.

The current API schema and PostgreSQL foundation schema are not column/type-compatible. This is a known blocker, not a successful cutover.

## 2. Plugin audit failure policy

**Policy: fail closed.** A provider result must not be returned to the caller unless its audit record is persisted. Audit sink failures raise `AuditPersistenceError` and block returning evidence. This applies to successful, denied, timeout, and failed provider outcomes; if the audit store is unavailable, the caller must treat the request as failed. Do not fall back to an in-memory-only audit log.

The current PostgreSQL writer is an injectable implementation, not a production-wired global runtime. Keep provider permissions empty by default. Before enabling any provider, the application bootstrap must explicitly create the PostgreSQL backend, verify migration 002, inject `PostgresPluginAuditWriter`, and fail startup if the configured durable audit store is unavailable.

Retention and tamper-resistance still require an operational policy: restricted insert-only application role, separate migration/retention role, monitored retention job, backups, and an integrity/reconciliation check. The current table is append-oriented by convention, not cryptographically tamper-proof.

## 3. Venue evidence report

Generate metadata-only report without network calls:

```powershell
$env:PYTHONPATH = "arb_bot"
python -m arbx.venue_verification --output venue-verification-report.json
```

Run bounded public metadata probes only when network access is intentionally approved:

```powershell
$env:PYTHONPATH = "arb_bot"
python -m arbx.venue_verification --public-network --timeout 5 --output venue-verification-report.json
```

This report never accepts API credentials and never calls private account endpoints, order methods, signing, withdrawals, or transfers. Public connectivity and observation freshness are reported separately from permission evidence. Reconnect behavior remains `unverified` until a repeatable reconnect test is implemented and recorded. Permission evidence remains `unverified` because public endpoints cannot establish API-key permissions. An adapter class existing is not proof of successful connectivity or operational readiness.

## 4. Simulation-only strategy validation

No real orders are part of this phase. The simulation acceptance suite must use deterministic fixtures and cover:
- fees, fee tiers, and conservative fee assumptions;
- slippage and market impact bounds;
- amount/price precision, minimum amount and minimum notional;
- partial fills, no fills, and rejected operations;
- stale and crossed/inconsistent order books;
- REST/WebSocket timeouts, reconnect gaps, and delayed observations;
- idempotent recovery after simulated process restart;
- conservative net-result accounting with no assumption of guaranteed profit.

Any missing, malformed, or stale input must reject the simulated opportunity. Passing simulation is not evidence of live profitability.

## 5. Operational release gates

- [ ] Create separate PostgreSQL migration, application, read-only reporting, and retention roles. The application role must not own schema objects or have DDL/role-management privileges.
- [ ] Run a backup and restore into an isolated disposable database; record command, timestamps, row counts, integrity checks, and recovery duration. Never restore over production as a test.
- [ ] Measure representative workloads and publish raw sample count plus p50/p95/p99 for API reads/writes, journal writes, and evidence audit writes. Do not publish estimates as measurements.
- [ ] Verify logs and reports contain no credentials, tokens, raw private payloads, or connection strings.
- [ ] Obtain human release approval only after CI, persistence parity, recovery, and security evidence are attached to the release.

Until these gates pass, status is **not approved for production PostgreSQL cutover or live execution**.


## 1B. Atomic worker-fenced journal append (partial implementation)

 `BackendTradeJournal.append_fenced` checks the durable session owner, mode, active lifecycle phase, worker ID, lease expiry, and fencing token in the same database transaction as the journal insert. The contract test covers lease takeover and rejects a stale worker without adding a row; the PostgreSQL integration test exercises the same invariant against a disposable database.

Important limits:
- The new method is an opt-in safe write path; it does not automatically rewrite all existing journal callers.
- Existing `append` remains for compatibility and must not be used for worker-originated writes once a supervised worker is integrated.
- Fenced variants now also exist for opportunity and venue-certification writes; worker call sites still need migration to these methods and disposable-PostgreSQL coverage.
- No worker loop or PostgreSQL start/stop route is enabled by this change.
- The new PostgreSQL integration test and full suite must pass in CI before this change is considered verified.

Do not treat this as proof that all consequential side effects are fenced. The release gate remains closed.


The evidence write methods are now `append_opportunity_fenced` and `record_certification_fenced`. Both use the same transaction-bound session/lease validation helper and reject stale worker identities. Contract coverage was added for stale-worker rejection and successful writes by the current worker. These additions have not yet been verified by a successful CI run.
