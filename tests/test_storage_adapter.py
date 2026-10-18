from __future__ import annotations
import pytest
from arbx.storage import PostgresBackend, SQLiteBackend, backend_from_environment

def test_sqlite_transaction_commits_and_fetches_rows(tmp_path):
    backend = SQLiteBackend(tmp_path / "nested" / "state.sqlite3")
    with backend.transaction() as session:
        session.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        session.execute("INSERT INTO records(value) VALUES (?)", ("alpha",))
    with backend.transaction() as session:
        assert session.fetch_one("SELECT value FROM records WHERE id=?", (1,)) == {"value": "alpha"}
        assert session.fetch_all("SELECT value FROM records") == [{"value": "alpha"}]

def test_sqlite_transaction_rolls_back_on_error(tmp_path):
    backend = SQLiteBackend(tmp_path / "state.sqlite3")
    with backend.transaction() as session: session.execute("CREATE TABLE records (value TEXT NOT NULL)")
    with pytest.raises(RuntimeError):
        with backend.transaction() as session:
            session.execute("INSERT INTO records VALUES (?)", ("not-committed",))
            raise RuntimeError("force rollback")
    with backend.transaction() as session: assert session.fetch_all("SELECT value FROM records") == []

def test_health_check_and_explicit_environment_selection(tmp_path, monkeypatch):
    assert SQLiteBackend(tmp_path / "state.sqlite3").health_check() is True
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ARBX_APP_DB", str(tmp_path / "env.sqlite3"))
    assert backend_from_environment().dialect == "sqlite"

def test_postgres_backend_rejects_non_postgres_url():
    with pytest.raises(ValueError): PostgresBackend("sqlite:///oops")
