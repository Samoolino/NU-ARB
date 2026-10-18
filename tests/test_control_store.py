from __future__ import annotations

import os
import pytest

from arbx.control_store import ControlPlaneStore
from arbx.storage import SQLiteBackend


def test_sqlite_control_store_signup_login_session_and_credentials(tmp_path):
    backend = SQLiteBackend(tmp_path / "control.sqlite3")
    store = ControlPlaneStore(backend)
    store.initialize()

    user = store.create_user(
        user_id="user-fixture",
        email="person@example.test",
        salt=b"salt-fixture",
        password_hash=b"hash-fixture",
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert user["email"] == "person@example.test"
    assert store.get_user_by_email("PERSON@example.test")["id"] == "user-fixture"

    store.create_session(
        token_hash="digest-fixture",
        user_id="user-fixture",
        expires_at=2_000_000_000,
    )
    assert store.get_session("digest-fixture")["user_id"] == "user-fixture"
    store.delete_session("digest-fixture")
    assert store.get_session("digest-fixture") is None

    store.save_credentials(
        user_id="user-fixture",
        exchange_id="binance",
        encrypted_credentials=b"ciphertext-only",
        auth_mode="hmac",
        state="VERIFIED",
        last_verified="2026-01-01T00:00:00+00:00",
        verification_json='{"evidence":{"scannerEligible":false}}',
    )
    row = store.get_credentials("user-fixture", "binance")
    assert row["encrypted_credentials"] == b"ciphertext-only"
    assert store.list_credentials("user-fixture")[0]["exchange_id"] == "binance"
    store.delete_credentials("user-fixture", "binance")
    assert store.get_credentials("user-fixture", "binance") is None


def test_store_requires_backend_schema_initialization(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "control.sqlite3"))
    with pytest.raises(Exception):
        store.get_user_by_email("missing@example.test")


def test_postgres_schema_uses_api_compatible_text_ids_and_binary_password_fields():
    from pathlib import Path

    migration = Path(__file__).parents[1] / "migrations" / "postgres" / "003_control_api_schema.sql"
    sql = migration.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS control_api_users" in sql
    assert "id TEXT PRIMARY KEY" in sql
    assert "salt BYTEA NOT NULL" in sql
    assert "password_hash BYTEA NOT NULL" in sql
    assert "encrypted_credentials BYTEA NOT NULL" in sql
