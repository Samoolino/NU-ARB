# PostgreSQL deployment and recovery runbook

## Current boundary

PostgreSQL is opt-in and the Control API migration is partial. SQLite remains the supported default. The current PR routes selected identity/session and exchange-credential operations through the injected `ControlPlaneStore` when PostgreSQL is selected, but engine ownership/lifecycle and remaining journal/execution paths have not completed parity. Those paths must fail closed rather than silently use SQLite.

Do not switch production traffic to PostgreSQL until all API queries, journal reads/writes, migrations, and parity tests are migrated and verified on the current commit.

## Configuration

- SQLite default: `ARBX_STORAGE_BACKEND=sqlite` and `ARBX_APP_DB=/data/arbx_app.sqlite3`.
- PostgreSQL opt-in for isolated testing/staging only: `ARBX_STORAGE_BACKEND=postgres` and `ARBX_DATABASE_URL` supplied by a secret manager.
- Never commit database URLs, credentials, exchange keys, or encryption keys.
- Use persistent storage. Run migrations as a separate deployment job, not from the API role.

## Least privilege

Use separate migration and application roles. The application role should have CONNECT, schema USAGE, and only necessary table/sequence privileges. It must not own the schema or create roles/databases. Require TLS for remote database connections and restrict network ingress.

Example role outline (review before use):

```sql
CREATE ROLE nuarb_migrator LOGIN;
CREATE ROLE nuarb_app LOGIN;
GRANT CONNECT ON DATABASE nuarb TO nuarb_app;
GRANT USAGE ON SCHEMA public TO nuarb_app;
-- Grant only required table and sequence privileges after schema migration.
```

## Readiness and liveness

- Liveness indicates process/event-loop responsiveness and should not fail solely due to DB outage.
- Readiness should fail if the selected DB cannot execute `SELECT 1` or required migrations are absent.
- Keep probe timeouts short; never expose connection strings or exception details.
- The generic storage `health_check()` is not yet fully wired to readiness; verify that the health endpoint matches the selected backend before a PostgreSQL deployment.

## Backup and restore

1. Use a dedicated backup identity and encrypted, access-controlled backup storage.
2. PostgreSQL backup: `pg_dump --format=custom --no-owner --file=nuarb.dump "$ARBX_DATABASE_URL"`.
3. Restore to an isolated empty database, never over the source: `createdb nuarb_restore_test; pg_restore --dbname=nuarb_restore_test --no-owner nuarb.dump`.
4. Verify migration versions, row counts, foreign keys, and application smoke tests.
5. Record backup age and restore duration; agree RPO/RTO with service owner.
6. Schedule restore drills. A successful backup command alone does not prove recovery.
7. For SQLite, use online backup API or a coordinated consistent snapshot; WAL writes make copying only the main file unsafe.

## Rollback

Keep the prior application artifact and database snapshot. Prefer forward-fix migrations; do not delete data to force a deploy green.

## Current release gate

Targeted CI on PR head `74b8f2bcbf0ca4837dfa90bf3830f84b9e5c65de` is green:
- [Hybrid Core run 39243409825](https://github.com/Samoolino/NU-ARB/actions/runs/39243409825): 11 passed; compile step succeeded.
- [PostgreSQL Integration run 39243409826](https://github.com/Samoolino/NU-ARB/actions/runs/39243409826): 100 passed, 1 warning; PostgreSQL 16 started and the configured test suite executed.

These runs establish success only for their configured test subsets. They do not complete persistence parity, full repository test coverage, independent review, or backup/restore evidence. Keep PostgreSQL opt-in and fail-closed until all cutover gates are evidenced. No production migration or live execution is approved by this runbook.
