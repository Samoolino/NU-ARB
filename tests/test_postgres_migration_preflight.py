from __future__ import annotations

import sqlite3

from scripts.postgres_migration_preflight import inspect_database


def _make_legacy_db(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, salt BLOB NOT NULL,
            password_hash BLOB NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE exchange_credentials (
            user_id TEXT NOT NULL REFERENCES users(id), exchange_id TEXT NOT NULL,
            encrypted_credentials BLOB NOT NULL, auth_mode TEXT NOT NULL, state TEXT NOT NULL,
            last_verified TEXT, verification_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(user_id, exchange_id)
        );
    """)
    db.commit()
    db.close()


def test_preflight_is_read_only_and_reports_safe_empty_legacy_schema(tmp_path):
    path = tmp_path / "app.sqlite3"
    _make_legacy_db(path)
    before = path.read_bytes()

    report = inspect_database(path)

    assert report["source"]["opened_read_only"] is True
    assert report["schema_family"] == "legacy_control_api"
    assert report["safe_to_plan_cutover"] is True
    assert report["blockers"] == []
    assert path.read_bytes() == before


def test_preflight_detects_duplicate_normalized_emails_without_exposing_rows(tmp_path):
    path = tmp_path / "app.sqlite3"
    _make_legacy_db(path)
    db = sqlite3.connect(path)
    for user_id, email in (("u1", "same@example.test"), ("u2", "SAME@example.test")):
        db.execute("INSERT INTO users VALUES(?,?,?,?,?)", (user_id, email, b"salt", b"hash", "now"))
    db.commit()
    db.close()

    report = inspect_database(path)

    assert report["safe_to_plan_cutover"] is False
    assert "duplicate emails exist after case/whitespace normalization" in report["blockers"]
    serialized = str(report)
    assert "same@example.test" not in serialized
    assert "u1" not in serialized
    assert "salt" not in serialized
    assert "hash" not in serialized


def test_preflight_detects_orphan_references_and_invalid_metadata(tmp_path):
    path = tmp_path / "app.sqlite3"
    _make_legacy_db(path)
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("INSERT INTO sessions VALUES(?,?,?)", ("digest-secret", "missing-user", 123))
    db.execute("""INSERT INTO exchange_credentials VALUES(?,?,?,?,?,?,?)""",
               ("missing-user", "fixture", b"ciphertext-secret", "test", "UNKNOWN", None, "not-json"))
    db.commit()
    db.close()

    report = inspect_database(path)

    assert report["safe_to_plan_cutover"] is False
    assert report["integrity_checks"]["orphan_sessions"] == 1
    assert report["integrity_checks"]["orphan_credentials"] == 1
    assert report["integrity_checks"]["invalid_verification_json_rows"] == 1
    assert "digest-secret" not in str(report)
    assert "ciphertext-secret" not in str(report)


def test_preflight_blocks_unknown_schema_family(tmp_path):
    path = tmp_path / "unknown.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY)")
    db.close()

    report = inspect_database(path)

    assert report["schema_family"] == "unrecognized_or_empty"
    assert report["safe_to_plan_cutover"] is False
    assert report["blockers"]
