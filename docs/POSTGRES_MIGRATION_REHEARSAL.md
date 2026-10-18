# PostgreSQL migration rehearsal gates

## Purpose and current status

The PostgreSQL control-plane schema and `ControlPlaneStore` are staged components. The FastAPI routes still use the legacy SQLite `_connect()` path. This checklist is a release gate, not authorization to cut over production. Do not run destructive migration commands against production as part of this rehearsal.

## 1. Source preflight (read-only)

Run against a **copied, access-controlled** SQLite database whenever possible:

```powershell
python scripts/postgres_migration_preflight.py --database path\to\arbx_app.sqlite3
```

- Exit `0`: structural checks permit planning a rehearsal only.
- Exit `1`: resolve reported blockers and rerun.
- Exit `2`: source could not be inspected.
- Preserve the JSON report as a release artifact only if your data-handling policy permits it; it contains table counts and structural findings, not row data.
- The tool never decrypts credentials and cannot establish that the configured encryption key is correct.

## 2. Generate a non-executable rehearsal plan

Run the plan generator against the same copied source database:

```powershell
python scripts/postgres_migration_rehearsal.py --database path\\to\\arbx_app.sqlite3
```

- Exit `0`: a metadata-only plan was generated; no destination was contacted and no records were imported.
- Exit `1`: the preflight is blocked or the source schema is incomplete.
- Exit `2`: source inspection failed; output intentionally omits filesystem exception messages.
- The JSON contains only table counts, static blocker/warning messages, and review steps. It does not contain a destination URL or row-level values.
- This command deliberately has no write mode. It does not implement or authorize a migration.

## 3. Disposable target requirements

- Use a new, isolated PostgreSQL database or disposable local service with no production data.
- Set `ARBX_TEST_POSTGRES_URL` only to a local test host accepted by the integration test (`localhost`, `127.0.0.1`, or `postgres`).
- Confirm the target is disposable and contains no valuable records before running migration integration tests; the suite applies migrations and writes fixture rows.
- Never put database URLs, passwords, encryption keys, credential payloads, or session tokens into logs or CI output.

## 4. Required migration rehearsal behavior

The rehearsal implementation must be a separate, explicit command and default to dry-run. Before any write-capable mode exists, tests must prove:

1. The source is opened read-only and the source file hash is unchanged.
2. Destination is explicitly supplied, local/disposable, and not the production URL; remote targets are rejected by default.
3. A transaction boundary covers destination import; any validation error rolls back all imported rows.
4. User IDs and email normalization rules are deterministic; collisions stop the run rather than merging accounts.
5. Session token hashes and expiry epochs are preserved byte-for-byte/value-for-value; expired sessions are not silently renewed.
6. Credential ciphertext bytes, auth mode, state, last-verification timestamp, and verification JSON are preserved without decrypting or re-encrypting them.
7. Source/destination counts and post-import referential integrity match for all three active tables.
8. A second dry run reports no planned changes; an actual rehearsal is repeatable only after explicit cleanup of the disposable target.
9. Output is metadata-only and never serializes rows, emails, identifiers, password material, token hashes, ciphertext, or verification payloads.
10. Rollback and restore are demonstrated from backups in a separate rehearsal before any production approval.

## 5. Go/no-go gates before route cutover

- Reviewed mapping between the legacy `users`/`sessions`/`exchange_credentials` schema and the new `control_api_*` schema.
- Automated source and destination parity checks, including binary credential preservation.
- Tests for failure injection, transaction rollback, idempotency, and collision handling.
- API contract tests for signup, login, logout, session expiry, credential list/remove, and permission-gated endpoints against both supported backends.
- Journal migration design: the engine journal currently opens per-user SQLite files directly and is not covered by this identity migration.
- Production audit writer wiring, audit integrity/retention acceptance, restore drill, performance checks, monitoring, and a documented rollback owner.
- Separate explicit release approval after all checks pass.

## Safety boundary

This work is limited to persistence migration planning, auditability, and simulation validation. It must not enable real orders, signing, withdrawals, transfers, or live trading. A successful preflight or test suite is not evidence of profitability or a production-safe cutover.
