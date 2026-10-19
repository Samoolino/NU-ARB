from pathlib import Path

import pytest

from arbx.postgres import _database_url, _migration_files


def test_database_url_requires_explicit_configuration(monkeypatch):
    monkeypatch.delenv("ARBX_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="must be set"):
        _database_url()


@pytest.mark.parametrize("url", ["sqlite:///tmp/test.db", "mysql://localhost/db", ""])
def test_database_url_rejects_unsupported_schemes(url):
    with pytest.raises(ValueError):
        _database_url(url)


def test_database_url_accepts_postgres_schemes():
    assert _database_url("postgresql://user:pass@localhost/db").startswith("postgresql://")
    assert _database_url("postgres://user:pass@localhost/db").startswith("postgres://")


def test_migration_inventory_is_sorted_and_includes_audit_and_control_api_schemas():
    files = _migration_files()
    assert files == sorted(files)
    assert [p.name for p in files] == [
        "001_initial_schema.sql",
        "002_plugin_audit.sql",
        "003_control_api_schema.sql",
        "004_engine_session_registry.sql",
        "005_engine_session_phase_constraints.sql",
        "006_engine_worker_leases.sql",
        "007_engine_session_owner_updated_index.sql",
        "008_engine_checkpoints.sql",
    ]
    sql = files[0].read_text(encoding="utf-8")
    assert "provider_evidence" in sql
    assert "encrypted_payload BYTEA NOT NULL" in sql
    assert "live_eligible BOOLEAN NOT NULL DEFAULT FALSE" in sql
    audit_sql = files[1].read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS plugin_audit_log" in audit_sql
    assert "payload_sha256" in audit_sql
    assert "payload JSON" not in audit_sql
    control_api_sql = files[2].read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS control_api_users" in control_api_sql
    assert "CREATE TABLE IF NOT EXISTS control_api_sessions" in control_api_sql
    assert "CREATE TABLE IF NOT EXISTS control_api_exchange_credentials" in control_api_sql
    engine_sessions_sql = files[3].read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS control_api_engine_sessions" in engine_sessions_sql
    assert "CHECK (mode IN ('paper', 'simulation'))" in engine_sessions_sql
    phase_sql = files[4].read_text(encoding="utf-8")
    assert "control_api_engine_sessions_phase_check" in phase_sql
    assert "'PREFLIGHT_FAILED'" in phase_sql
    leases_sql = files[5].read_text(encoding="utf-8")
    assert "control_api_engine_worker_leases" in leases_sql
    assert "fencing_token BIGINT NOT NULL CHECK (fencing_token >= 1)" in leases_sql
    assert "ON DELETE CASCADE" in leases_sql
    latest_index_sql = files[6].read_text(encoding="utf-8")
    assert "control_api_engine_sessions_owner_updated_idx" in latest_index_sql
    assert "updated_at DESC, created_at DESC, session_id ASC" in latest_index_sql


def test_migration_inventory_rejects_duplicate_versions(tmp_path: Path):
    (tmp_path / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "001_second.sql").write_text("SELECT 2;", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        _migration_files(tmp_path)



def test_postgres_migrations_cover_runtime_control_store_and_journal_columns():
    """Catch schema drift before any PostgreSQL runtime cutover is attempted."""
    files = {path.name: path.read_text(encoding="utf-8") for path in _migration_files()}
    control = files["003_control_api_schema.sql"]
    registry = files["004_engine_session_registry.sql"]
    journal = files["001_initial_schema.sql"]

    for column in (
        "id TEXT PRIMARY KEY",
        "email TEXT NOT NULL UNIQUE",
        "salt BYTEA NOT NULL",
        "password_hash BYTEA NOT NULL",
        "created_at TEXT NOT NULL",
    ):
        assert column in control

    for column in (
        "token_hash TEXT PRIMARY KEY",
        "user_id TEXT NOT NULL REFERENCES control_api_users(id) ON DELETE CASCADE",
        "expires_at BIGINT NOT NULL",
    ):
        assert column in control

    for column in (
        "session_id TEXT PRIMARY KEY",
        "owner_user_id TEXT NOT NULL REFERENCES control_api_users(id) ON DELETE CASCADE",
        "mode TEXT NOT NULL CHECK (mode IN ('paper', 'simulation'))",
        "phase TEXT NOT NULL",
        "stopped_at TEXT",
        "error_code TEXT",
        "metadata_json TEXT NOT NULL DEFAULT '{}'",
    ):
        assert column in registry

    # BackendTradeJournal writes/reads these fields. The migration must keep
    # the names and JSONB-compatible columns required by the runtime contract.
    for column in (
        "session_id TEXT NOT NULL",
        "timestamp TIMESTAMPTZ NOT NULL DEFAULT now()",
        "mode TEXT NOT NULL",
        "exchange_a TEXT NOT NULL",
        "strategy TEXT NOT NULL",
        "path JSONB NOT NULL DEFAULT '[]'::jsonb",
        "symbols JSONB NOT NULL DEFAULT '[]'::jsonb",
        "net_pnl DOUBLE PRECISION NOT NULL DEFAULT 0",
        "execution_status TEXT NOT NULL",
        "verification_status TEXT NOT NULL",
        "cumulative_realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0",
    ):
        assert column in journal
