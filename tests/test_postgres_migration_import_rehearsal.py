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


class FakePostgres:
    TABLES = {
        "control_api_users": ("id,email,salt,password_hash,created_at", 5),
        "control_api_sessions": ("token_hash,user_id,expires_at", 3),
        "control_api_exchange_credentials": (
            "user_id,exchange_id,encrypted_credentials,auth_mode,state,last_verified,verification_json", 7
        ),
    }

    def __init__(self, *, corrupt=False, prepopulate=False):
        self.rows = {name: [] for name in self.TABLES}
        if prepopulate:
            self.rows["control_api_users"].append(("existing", "existing@example.test", b"x", b"y", "now"))
        self.corrupt = corrupt
        self.rollback_seen = False

    def connect(self, _url):
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def transaction(self):
        return FakeTransaction(self.db)

    def cursor(self):
        return FakeCursor(self.db)


class FakeTransaction:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.before = {key: list(value) for key, value in self.db.rows.items()}
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.db.rows = self.before
            self.db.rollback_seen = True
        return False


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT COUNT(*) FROM "):
            table = normalized.rsplit(" ", 1)[-1]
            self.result = (len(self.db.rows[table]),)
            return
        if normalized.startswith("INSERT INTO "):
            table = normalized.split()[2].split("(")[0]
            row = tuple(params)
            if self.db.corrupt and table == "control_api_users":
                row = (*row[:2], b"corrupted", *row[3:])
            self.db.rows[table].append(row)
            return
        if normalized.startswith("SELECT ") and " FROM " in normalized:
            fields, table = normalized[7:].split(" FROM ", 1)
            table = table.split()[0]
            columns = [part.strip() for part in fields.split(",")]
            source_columns = [part.strip() for part in self.db.TABLES[table][0].split(",")]
            indexes = [source_columns.index(column) for column in columns]
            values = [tuple(row[index] for index in indexes) for row in self.db.rows[table]]
            if table == "control_api_users":
                values.sort(key=lambda row: row[0])
            elif table == "control_api_sessions":
                values.sort(key=lambda row: row[0])
            else:
                values.sort(key=lambda row: (row[0], row[1]))
            self.result = values
            return
        raise AssertionError(f"Unexpected SQL in test fake: {normalized}")

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result


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


def test_import_rehearsal_rolls_back_all_rows_after_injected_failure(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    make_source(source)
    fake = FakePostgres()
    monkeypatch.setattr("scripts.postgres_migration_import_rehearsal.psycopg.connect", fake.connect)

    with pytest.raises(RuntimeError, match="Injected import rehearsal failure"):
        import_control_plane(
            source_path=str(source),
            target_url="postgresql://tester:secret@localhost:5432/nu_arb_rehearsal",
            execute=True,
            fail_after_inserts=2,
        )

    assert fake.rollback_seen is True
    assert all(rows == [] for rows in fake.rows.values())


def test_import_rehearsal_rejects_existing_destination_rows(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    make_source(source)
    fake = FakePostgres(prepopulate=True)
    monkeypatch.setattr("scripts.postgres_migration_import_rehearsal.psycopg.connect", fake.connect)

    with pytest.raises(ValueError, match="must be empty"):
        import_control_plane(
            source_path=str(source),
            target_url="postgresql://tester:secret@localhost:5432/nu_arb_rehearsal",
            execute=True,
        )

    assert fake.rollback_seen is True
    assert len(fake.rows["control_api_users"]) == 1


def test_import_rehearsal_checks_exact_row_parity_not_only_counts(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    make_source(source)
    fake = FakePostgres(corrupt=True)
    monkeypatch.setattr("scripts.postgres_migration_import_rehearsal.psycopg.connect", fake.connect)

    with pytest.raises(ValueError, match="row data do not match"):
        import_control_plane(
            source_path=str(source),
            target_url="postgresql://tester:secret@localhost:5432/nu_arb_rehearsal",
            execute=True,
        )

    assert fake.rollback_seen is True
    assert all(rows == [] for rows in fake.rows.values())
