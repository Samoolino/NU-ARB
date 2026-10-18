from __future__ import annotations

import sqlite3

import pytest

from scripts.postgres_migration_import_rehearsal import import_control_plane


def make_source(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL, salt BLOB NOT NULL,
                password_hash BLOB NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE exchange_credentials (
                user_id TEXT NOT NULL, exchange_id TEXT NOT NULL,
                encrypted_credentials BLOB NOT NULL, auth_mode TEXT NOT NULL,
                state TEXT NOT NULL, last_verified TEXT, verification_json TEXT NOT NULL
            );
            INSERT INTO users VALUES ('u1','pilot@example.test',X'0102',X'0304','2026-01-01T00:00:00Z');
            INSERT INTO sessions VALUES ('token-hash-1','u1',1900000000);
            INSERT INTO exchange_credentials VALUES
                ('u1','sample',X'000102FF','read_only','verified',NULL,'{"ok":true}');
        """)


def test_import_rehearsal_defaults_to_dry_run_and_never_connects(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    make_source(source)

    def fail_connect(*args, **kwargs):
        pytest.fail("dry-run must not open a PostgreSQL connection")

    monkeypatch.setattr("scripts.postgres_migration_import_rehearsal.psycopg.connect", fail_connect)
    result = import_control_plane(
        source_path=str(source),
        target_url="postgresql://tester:secret@localhost:5432/nu_arb_rehearsal",
        execute=False,
    )

    assert result["mode"] == "dry_run"
    assert result["writes_enabled"] is False
    assert result["source_counts"] == {
        "users": 1, "sessions": 1, "exchange_credentials": 1
    }


def test_import_rehearsal_rejects_remote_target_before_connecting(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    make_source(source)

    def fail_connect(*args, **kwargs):
        pytest.fail("remote target must be rejected before connecting")

    monkeypatch.setattr("scripts.postgres_migration_import_rehearsal.psycopg.connect", fail_connect)
    with pytest.raises(ValueError):
        import_control_plane(
            source_path=str(source),
            target_url="postgresql://tester:secret@remote.example.com:5432/nu_arb_rehearsal",
            execute=True,
        )
