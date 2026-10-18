# PostgreSQL foundation — Phase A increment 1

## Status

This increment adds an explicit PostgreSQL migration runner, an initial schema,
and unit tests for configuration and migration inventory. It does not yet
switch the control API or execution journal from SQLite, and it does not claim
a successful PostgreSQL integration test. Existing deployments remain on their
current ARBX_APP_DB SQLite configuration.

## Run

Install the updated arb_bot/requirements.txt, set ARBX_DATABASE_URL to a
PostgreSQL connection URL, and call arbx.postgres.run_postgres_migrations()
from a reviewed deployment/migration step. The runner refuses missing or
non-PostgreSQL URLs, applies checked-in numbered migrations in order, and
records applied versions in schema_migrations. Each migration and its ledger
entry run in a transaction.

Example URL shape (replace placeholders locally; do not commit credentials):

    postgresql://USER:PASSWORD@HOST:5432/DATABASE

## Schema and safety notes

- Credential material is stored as encrypted bytes only; encryption/decryption
  remains the responsibility of the existing application boundary.
- Venue eligibility defaults to false; catalog presence is not certification.
- Provider evidence distinguishes ok, unavailable, stale, and error.
- Migration SQL is repository-owned and must never contain interpolated user or
  provider data.
- Do not point this migration runner at production until backup/restore,
  permissions, and migration review are complete.
- Live order execution, transaction signing, and fund transfers are not part of
  this phase. No profitability or no-loss guarantee is implied.

## Next Phase A work

Integrate repositories into the existing SQLite-specific API and journal call
sites behind a tested storage interface, add a real PostgreSQL integration test
service, then add Compose health/persistence configuration. Do not change the
runtime database default until parity and migration/rollback tests pass.
