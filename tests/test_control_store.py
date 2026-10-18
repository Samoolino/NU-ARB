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


def test_account_and_initial_session_are_created_atomically(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "atomic-control.sqlite3"))
    store.initialize()

    user = store.create_user_with_session(
        user_id="atomic-user", email="Atomic@Example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
        token_hash="atomic-session-digest", expires_at=2_000_000_000,
    )

    assert user["email"] == "atomic@example.test"
    assert store.get_session("atomic-session-digest") == {
        "user_id": "atomic-user", "expires_at": 2_000_000_000,
    }


def test_account_creation_rolls_back_if_initial_session_insert_fails(tmp_path):
    import sqlite3

    store = ControlPlaneStore(SQLiteBackend(tmp_path / "atomic-rollback.sqlite3"))
    store.initialize()
    store.create_user_with_session(
        user_id="existing-user", email="existing@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
        token_hash="already-used-digest", expires_at=2_000_000_000,
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.create_user_with_session(
            user_id="must-rollback", email="rollback@example.test", salt=b"salt",
            password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
            token_hash="already-used-digest", expires_at=2_000_000_000,
        )

    assert store.get_user_by_email("rollback@example.test") is None
    assert store.get_session("already-used-digest")["user_id"] == "existing-user"


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



def test_postgres_control_store_contract_when_disposable_database_is_configured():
    database_url = os.getenv("ARBX_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("set ARBX_TEST_POSTGRES_URL to a disposable local PostgreSQL database")
    from urllib.parse import urlparse
    from arbx.postgres import run_postgres_migrations
    from arbx.storage import PostgresBackend

    host = urlparse(database_url).hostname
    if host not in {"localhost", "127.0.0.1", "postgres"}:
        pytest.skip("refusing to mutate a non-local database in integration tests")
    run_postgres_migrations(database_url)
    store = ControlPlaneStore(PostgresBackend(database_url))
    user_id = "control-store-integration-fixture"
    email = "control-store-integration@example.test"
    # Use a deterministic fixture and clean it up through cascading foreign keys.
    with store.backend.transaction() as session:
        session.execute("DELETE FROM control_api_users WHERE id=%s", (user_id,))
        session.execute("DELETE FROM control_api_users WHERE email=%s", (email,))
    store.create_user(
        user_id=user_id, email=email, salt=b"test-salt",
        password_hash=b"test-password-hash", created_at="2026-01-01T00:00:00+00:00",
    )
    assert store.get_user_by_email(email)["id"] == user_id
    store.create_session(token_hash="control-store-session-fixture", user_id=user_id, expires_at=2_000_000_000)
    assert store.get_session("control-store-session-fixture")["user_id"] == user_id
    store.save_credentials(
        user_id=user_id, exchange_id="fixture", encrypted_credentials=b"ciphertext",
        auth_mode="test", state="NOT_VERIFIED", last_verified=None, verification_json="{}",
    )
    assert store.get_credentials(user_id, "fixture")["encrypted_credentials"] == b"ciphertext"
    store.delete_credentials(user_id, "fixture")
    store.delete_session("control-store-session-fixture")
    with store.backend.transaction() as session:
        session.execute("DELETE FROM control_api_users WHERE id=%s", (user_id,))



def test_store_rolls_back_duplicate_email_without_creating_second_user(tmp_path):
    backend = SQLiteBackend(tmp_path / "control.sqlite3")
    store = ControlPlaneStore(backend)
    store.initialize()
    store.create_user(
        user_id="first-user", email="same@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
    )

    with pytest.raises(Exception):
        store.create_user(
            user_id="second-user", email="SAME@example.test", salt=b"salt2",
            password_hash=b"hash2", created_at="2026-01-02T00:00:00+00:00",
        )

    assert store.get_user("second-user") is None
    assert store.get_user_by_email("same@example.test")["id"] == "first-user"


def test_deleting_user_cascades_sessions_and_credentials(tmp_path):
    backend = SQLiteBackend(tmp_path / "control.sqlite3")
    store = ControlPlaneStore(backend)
    store.initialize()
    store.create_user(
        user_id="cascade-user", email="cascade@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
    )
    store.create_session(token_hash="cascade-session", user_id="cascade-user", expires_at=2_000_000_000)
    store.save_credentials(
        user_id="cascade-user", exchange_id="fixture", encrypted_credentials=b"ciphertext",
        auth_mode="test", state="NOT_VERIFIED", last_verified=None, verification_json="{}",
    )

    with backend.transaction() as session:
        session.execute("DELETE FROM control_api_users WHERE id=?", ("cascade-user",))

    assert store.get_session("cascade-session") is None
    assert store.get_credentials("cascade-user", "fixture") is None


def test_get_valid_session_rejects_expired_and_returns_active_session(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "control.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="session-user", email="session@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
    )
    store.create_session(token_hash="expired-token", user_id="session-user", expires_at=99)
    store.create_session(token_hash="active-token", user_id="session-user", expires_at=101)

    assert store.get_valid_session("expired-token", now_epoch=100) is None
    active = store.get_valid_session("active-token", now_epoch=100)
    assert active == {"user_id": "session-user", "expires_at": 101}


def test_purge_expired_sessions_removes_only_sessions_at_or_before_now(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "control.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="purge-user", email="purge@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
    )
    store.create_session(token_hash="old-session", user_id="purge-user", expires_at=99)
    store.create_session(token_hash="boundary-session", user_id="purge-user", expires_at=100)
    store.create_session(token_hash="future-session", user_id="purge-user", expires_at=101)

    assert store.purge_expired_sessions(now_epoch=100) == 2
    assert store.get_session("old-session") is None
    assert store.get_session("boundary-session") is None
    assert store.get_session("future-session")["expires_at"] == 101


def test_revoke_all_user_sessions_does_not_affect_other_users(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "control.sqlite3"))
    store.initialize()
    for user_id, email in (
        ("revoke-user", "revoke@example.test"),
        ("keep-user", "keep@example.test"),
    ):
        store.create_user(
            user_id=user_id, email=email, salt=b"salt",
            password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
        )
    store.create_session(token_hash="revoke-session-a", user_id="revoke-user", expires_at=200)
    store.create_session(token_hash="revoke-session-b", user_id="revoke-user", expires_at=300)
    store.create_session(token_hash="keep-session", user_id="keep-user", expires_at=400)

    assert store.revoke_user_sessions("revoke-user") == 2
    assert store.get_session("revoke-session-a") is None
    assert store.get_session("revoke-session-b") is None
    assert store.get_session("keep-session")["user_id"] == "keep-user"



def test_session_expiry_and_revocation_are_user_scoped(tmp_path):
    backend = SQLiteBackend(tmp_path / "session-lifecycle.sqlite3")
    store = ControlPlaneStore(backend)
    store.initialize()
    for user_id, email in (
        ("user-one", "one@example.test"),
        ("user-two", "two@example.test"),
    ):
        store.create_user(
            user_id=user_id,
            email=email,
            salt=b"salt",
            password_hash=b"hash",
            created_at="2026-01-01T00:00:00+00:00",
        )
    store.create_session(token_hash="one-valid", user_id="user-one", expires_at=200)
    store.create_session(token_hash="one-expired", user_id="user-one", expires_at=100)
    store.create_session(token_hash="two-valid", user_id="user-two", expires_at=300)

    assert store.get_valid_session("one-valid", now_epoch=199)["user_id"] == "user-one"
    assert store.get_valid_session("one-valid", now_epoch=200) is None
    assert store.get_valid_session("one-expired", now_epoch=100) is None
    assert store.purge_expired_sessions(now_epoch=100) == 1
    assert store.revoke_user_sessions("user-one") == 1
    assert store.get_session("one-valid") is None
    assert store.get_session("two-valid")["user_id"] == "user-two"


def test_credentials_are_isolated_between_users_and_deletion_is_scoped(tmp_path):
    backend = SQLiteBackend(tmp_path / "credential-isolation.sqlite3")
    store = ControlPlaneStore(backend)
    store.initialize()
    for user_id, email in (
        ("user-one", "one@example.test"),
        ("user-two", "two@example.test"),
    ):
        store.create_user(
            user_id=user_id,
            email=email,
            salt=b"salt",
            password_hash=b"hash",
            created_at="2026-01-01T00:00:00+00:00",
        )
    store.save_credentials(
        user_id="user-one", exchange_id="binance", encrypted_credentials=b"cipher-one",
        auth_mode="hmac", state="VERIFIED", last_verified=None, verification_json="{}",
    )
    store.save_credentials(
        user_id="user-two", exchange_id="binance", encrypted_credentials=b"cipher-two",
        auth_mode="hmac", state="VERIFIED", last_verified=None, verification_json="{}",
    )

    assert store.get_credentials("user-one", "binance")["encrypted_credentials"] == b"cipher-one"
    assert store.get_credentials("user-two", "binance")["encrypted_credentials"] == b"cipher-two"
    store.delete_credentials("user-one", "binance")
    assert store.get_credentials("user-one", "binance") is None
    assert store.get_credentials("user-two", "binance")["encrypted_credentials"] == b"cipher-two"



def test_credential_listing_excludes_ciphertext_and_upsert_replaces_metadata(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "credential-listing.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="listing-user", email="listing@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
    )

    store.save_credentials(
        user_id="listing-user", exchange_id="binance", encrypted_credentials=b"secret-ciphertext",
        auth_mode="hmac", state="NOT_VERIFIED", last_verified=None,
        verification_json='{"version":1}',
    )
    listed = store.list_credentials("listing-user")
    assert listed == [{
        "exchange_id": "binance",
        "auth_mode": "hmac",
        "state": "NOT_VERIFIED",
        "last_verified": None,
        "verification_json": '{"version":1}',
    }]
    assert all("encrypted_credentials" not in row for row in listed)

    store.save_credentials(
        user_id="listing-user", exchange_id="binance", encrypted_credentials=b"rotated-ciphertext",
        auth_mode="hmac", state="VERIFIED", last_verified="2026-01-02T00:00:00+00:00",
        verification_json='{"version":2}',
    )
    saved = store.get_credentials("listing-user", "binance")
    assert saved["encrypted_credentials"] == b"rotated-ciphertext"
    assert saved["state"] == "VERIFIED"
    assert saved["verification_json"] == '{"version":2}'
