# PostgreSQL Control API Persistence Cutover Plan

Status: **partial route migration committed; targeted current-head CI is green, full cutover remains blocked**  
Target branch: `phase-a-postgres-foundation`  
Scope: Control API identity, sessions, and encrypted exchange-credential metadata only.

## Current verified state

- Current PR head verified by CI: `246c6eb4992fc250583137d5db2f9e8e1a68e681`.
- [Hybrid Core run 39243409825](https://github.com/Samoolino/NU-ARB/actions/runs/39243409825) completed successfully: **11 passed**. Python compilation also completed successfully.
- [PostgreSQL Integration run 39243409826](https://github.com/Samoolino/NU-ARB/actions/runs/39243409826) completed successfully: **100 passed, 1 warning**. The job started PostgreSQL 16, installed dependencies, compiled Python, and ran the configured PostgreSQL/control API suite.
- These results verify the two targeted workflows on the current head. They do not prove complete route parity, full repository test coverage, a successful production migration, or live execution readiness.
- [Hybrid Core run 39289213632](https://github.com/Samoolino/NU-ARB/actions/runs/39289213632) passed on the current head; its compile and Hybrid Core test steps succeeded.
- [PostgreSQL Integration run 39289214143](https://github.com/Samoolino/NU-ARB/actions/runs/39289214143) passed on the current head; PostgreSQL/control API tests and the complete repository test suite succeeded.
- Added `tests/test_postgres_engine_routes_fail_closed.py` in commit `246c6eb4992fc250583137d5db2f9e8e1a68e681` to assert that engine state, journal, start, and stop endpoints return HTTP 503 when PostgreSQL is selected before those paths are migrated, and that no SQLite fallback database is created.
- The current implementation has a `ControlPlaneStore` and versioned PostgreSQL migrations. FastAPI route migration is partial: signup/login/logout/me, exchange listing, capital-source listing, exchange verification persistence, and credential deletion use the injected store when PostgreSQL is selected; SQLite retains the legacy path.
- The login email normalization regression is covered by the passing PostgreSQL integration suite. PostgreSQL log messages include expected constraint errors from negative test cases; the job still completed successfully.
- Engine ownership/lifecycle and remaining journal/execution persistence paths require additional parity review. The legacy `TradeJournal` is SQLite-backed; the `BackendTradeJournal` exists and the journal factory can select it, but application call sites and all data paths still require audit and contract evidence.
- Keep PostgreSQL fail-closed for routes that have not completed parity. Do not infer readiness from backend construction or the passing targeted suites.
- No production migration, deployment, credential import, or live execution is authorized by this plan.

## Cutover gates

### Gate 1 — API/store contract tests

Required before route cutover. Add integration tests against public FastAPI endpoints with an injected persistence backend. Cover:
- signup writes one user and a hashed session token; password material is never returned;
- duplicate, case-insensitive email signup maps to HTTP 409 and leaves one user;
- login validates the same PBKDF2 parameters and creates a session;
- `/auth/me` accepts only unexpired sessions and rejects the exact expiry boundary;
- logout revokes only the presented session and refuses unsafe logout while an owned engine task is active;
- credentials are encrypted before persistence, scoped by user, and never returned in plaintext;
- credential verification metadata round-trips without leaking credential fields;
- PostgreSQL backend failure returns sanitized 503 and never creates or writes an SQLite fallback file;
- SQLite and PostgreSQL return the same externally visible status codes and response shapes.

Use a disposable local PostgreSQL service in CI only. Tests must refuse non-local or production-looking database hosts and use unique fixture IDs with cleanup. Add failure-path and transaction rollback tests, not only happy paths.

### Gate 2 — route/store adapter

Implementation order (keep the fail-closed guard until every step is complete):
1. Introduce a request-scoped control-plane persistence dependency and allow tests to inject a backend/store without mutating module globals.
2. Migrate signup and login together so the account and first session are created atomically; preserve PBKDF2 settings, cookie attributes, response bodies, and 409/401 behavior.
3. Migrate session lookup, expiry cleanup, `/auth/me`, and logout to the same store; verify engine ownership checks use that same session identity.
4. Migrate exchange credential reads/writes/deletes through the store, retaining encryption at the API boundary and returning metadata-only listings.
5. Migrate engine state, journal, capital-source, start, and stop identity/ownership reads as one coordinated set. Do not enable PostgreSQL until these routes no longer use the legacy `_connect()` tables.

- Inject `ControlPlaneStore` through one explicit factory/dependency rather than branching SQL in every route.
- Make the adapter own identity/session/credential persistence. Avoid changing market/exchange verification behavior in the same patch.
- Normalize SQLite/PostgreSQL uniqueness violations at the storage boundary so API responses are backend-independent.
- Use `create_user_with_session` for atomic account creation and initial session creation, or document and test a recoverable transaction boundary.
- Keep SQL parameterized; never interpolate user-controlled values into SQL.
- Keep encrypted credential bytes and session token hashes as the only persisted forms. Never log secrets, raw session tokens, or database URLs.
- Do not initialize or mutate the PostgreSQL schema implicitly at application startup; use the versioned migration runner.

### Gate 3 — test and review

Required checks:
- focused API/store contract tests on SQLite and PostgreSQL;
- full PostgreSQL integration workflow and the repository's normal test suite;
- schema migration preflight and rehearsal against a disposable database;
- static review for remaining route-level `_connect()` calls and legacy table references;
- review of transaction boundaries, uniqueness handling, session expiry, ownership isolation, and sanitized error responses;
- compare endpoint response contracts against existing SQLite behavior.

The fail-closed guard stays enabled if any gate is incomplete or any test is skipped unexpectedly.

### Gate 4 — staged operational rehearsal

1. Snapshot and verify a restorable backup of a non-production SQLite fixture.
2. Run the importer in dry-run mode; verify row counts, case-insensitive email collisions, foreign keys, ciphertext byte preservation, and session expiry handling.
3. Import into an isolated PostgreSQL database and compare source/target counts and deterministic row hashes for non-secret metadata. Never print or hash raw passwords, tokens, or decrypted credentials into logs.
4. Test rollback by discarding the disposable target and prove the SQLite source remains unchanged.
5. Perform a staging-only cutover with a documented rollback switch and an observation window.
6. Production migration requires a separate explicit approval, verified backup/restore, reviewed migration evidence, and an operational rollback plan. This document does not grant that approval.

## Rollback and safety invariants

- Until all gates pass, SQLite remains the supported Control API backend.
- Never silently fall back from configured PostgreSQL to SQLite.
- Never run import/migration commands against production as part of tests or rehearsal.
- Never enable live trading as part of persistence validation.
- Keep the draft PR open and unmerged until independent review and required checks are complete.

## Completion checklist

- [ ] API/store integration tests written first and demonstrated to fail against the current unwired routes.
- [x] Injectable `control_plane_store_from_environment(backend=...)` seam and `get_control_plane_store()` FastAPI dependency entry point added; injection does not initialize schema implicitly.
- [x] Atomic store-level account/session transaction and rollback tests implemented.
- [x] Foundation and fail-closed safety CI workflows passed for historical HEAD `cd1ecd9f877895700b50b715bfc22cd0879bdef9`.
- [ ] Recover CI job execution and verify current HEAD before further cutover. Latest jobs still expose no steps or logs; do not bypass required checks.
- [ ] Full route adapter implemented with transaction and error parity. Current partial migration covers signup/login/logout/me, exchange listing, capital-source listing, exchange verification persistence, and credential deletion. Engine and execution routes still have legacy persistence paths.
- [ ] SQLite and disposable PostgreSQL contract suites pass.
- [ ] No route-level legacy persistence paths remain for all intended entities. The current change is deliberately partial; legacy engine/execution persistence remains and must fail closed under PostgreSQL selection.
- [ ] Migration preflight/rehearsal evidence attached to the PR.
- [ ] Independent review complete.
- [ ] Explicit operational approval for any real production cutover.
