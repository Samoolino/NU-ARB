# PostgreSQL Control API Persistence Cutover Plan

Status: **partial route migration committed; full cutover blocked pending CI recovery and current-HEAD verification**  
Target branch: `phase-a-postgres-foundation`  
Scope: Control API identity, sessions, and encrypted exchange-credential metadata only.

## Current verified state

- Current PR head: `a6b65cb09392167f5ff215019ee7372d9021028a`; current-head tests are **not verified**.
- The latest GitHub Actions jobs on this head (PostgreSQL Integration run [39219149174](https://github.com/Samoolino/NU-ARB/actions/runs/39219149174), Hybrid Core run [39219149070](https://github.com/Samoolino/NU-ARB/actions/runs/39219149070)) failed in about two seconds. The Jobs API reports `runner_id: 0`, empty `steps`, and no log URL. This is evidence that the runner/job did not expose normal execution steps; it is not evidence that pytest ran and failed. The exact infrastructure cause is unknown.
- The same failure-before-steps pattern is present in earlier runs, including [PostgreSQL Integration 39218920069](https://github.com/Samoolino/NU-ARB/actions/runs/39218920069) and [Hybrid Core 39218920325](https://github.com/Samoolino/NU-ARB/actions/runs/39218920325). Log retrieval previously returned `BlobNotFound`.
- Workflow definitions use ordinary `ubuntu-latest`, checkout, Python setup, dependency install, compile, and pytest steps. Current evidence does not justify changing workflows, weakening branch protection, or treating these jobs as successful. Recovery requires repository Actions/runner diagnostics from an administrator or GitHub support.
- Historical code head `cd1ecd9f877895700b50b715bfc22cd0879bdef9` passed [Hybrid Core](https://github.com/Samoolino/NU-ARB/actions/runs/39122717168) and [PostgreSQL Integration](https://github.com/Samoolino/NU-ARB/actions/runs/39122716854). That historical result does not verify current HEAD or prove PostgreSQL-backed public routes.
- `ControlPlaneStore`, migrations, SQLite compatibility, and regression tests exist. FastAPI route migration is partial: signup/login/logout/me, exchange listing, capital-source listing, exchange verification persistence, and credential deletion use the injected store when PostgreSQL is selected; SQLite retains the legacy schema path.
- Engine ownership, engine lifecycle, and remaining journal/execution paths have not completed persistence parity. PostgreSQL selection must remain fail-closed for routes not migrated.
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
