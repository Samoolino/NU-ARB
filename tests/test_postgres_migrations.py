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


def test_migration_inventory_rejects_duplicate_versions(tmp_path: Path):
    (tmp_path / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "001_second.sql").write_text("SELECT 2;", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        _migration_files(tmp_path)
