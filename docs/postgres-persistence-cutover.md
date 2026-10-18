# PostgreSQL Control API Persistence Cutover Plan

Status: **foundation verified; route cutover blocked**  
Target branch: `phase-a-postgres-foundation`  
Scope: Control API identity, sessions, and encrypted exchange-credential metadata only.

## Current verified state

- The latest verified code HEAD `a11a0a88530a5419c9b070f82cd1c19d4a3881fc` passed [Hybrid Core Tests](https://github.com/Samoolino/NU-ARB/actions/runs/39120489898) and [PostgreSQL Integration](https://github.com/Samoolino/NU-ARB/actions/runs/39120489992). These validate the injectable dependency seam and preparatory store/fail-closed tests; they do not demonstrate PostgreSQL-backed public routes.
- `ControlPlaneStore` and PostgreSQL schema migrations exist.
- Atomic account-plus-initial-session persistence is implemented in `ControlPlaneStore.create_user_with_session(...)`; tests cover successful creation and rollback when session insertion fails.
- FastAPI routes in `arb_bot/arbx/web_api.py` still use the legacy SQLite schema through `_connect()`.
- Selecting `ARBX_STORAGE_BACKEND=postgres` intentionally returns HTTP 503 for route requests; do not remove this guard until route parity is tested.
- The fail-closed regression test proves that the current unwired routes return sanitized HTTP 503 for configured PostgreSQL without creating a SQLite fallback database.
- Store-level credential-listing tests prove ciphertext is omitted from listing responses, metadata upserts replace prior values, and each user's listing is isolated from other users.
- The passing workflows validate the committed foundation and regression tests; they do **not** prove the public API routes are PostgreSQL-backed.
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
- [x] Foundation and fail-closed safety CI workflows passed for HEAD `b4c6410275c52ef07ace234d3862f4203d7a5ddf`.
- [ ] Route adapter implemented with transaction and error parity.
- [ ] SQLite and disposable PostgreSQL contract suites pass.
- [ ] No route-level legacy persistence paths remain for the migrated entities.
- [ ] Migration preflight/rehearsal evidence attached to the PR.
- [ ] Independent review complete.
- [ ] Explicit operational approval for any real production cutover.
