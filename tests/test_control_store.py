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



def test_credential_listing_is_scoped_to_requesting_user(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "credential-list-scope.sqlite3"))
    store.initialize()
    for user_id, email in (
        ("owner-user", "owner@example.test"),
        ("other-user", "other@example.test"),
    ):
        store.create_user(
            user_id=user_id, email=email, salt=b"salt",
            password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
        )
    store.save_credentials(
        user_id="owner-user", exchange_id="binance", encrypted_credentials=b"owner-cipher",
        auth_mode="hmac", state="VERIFIED", last_verified=None, verification_json="{}",
    )
    store.save_credentials(
        user_id="other-user", exchange_id="bybit", encrypted_credentials=b"other-cipher",
        auth_mode="hmac", state="VERIFIED", last_verified=None, verification_json="{}",
    )

    assert [row["exchange_id"] for row in store.list_credentials("owner-user")] == ["binance"]
    assert [row["exchange_id"] for row in store.list_credentials("other-user")] == ["bybit"]
    assert store.list_credentials("missing-user") == []

def test_credential_listing_is_sorted_stably_by_exchange_id(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "credential-list-order.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="ordered-user", email="ordered@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
    )

    for exchange_id in ("kraken", "binance", "coinbase"):
        store.save_credentials(
            user_id="ordered-user", exchange_id=exchange_id,
            encrypted_credentials=(exchange_id + "-cipher").encode(),
            auth_mode="test", state="NOT_VERIFIED", last_verified=None,
            verification_json="{}",
        )

    assert [row["exchange_id"] for row in store.list_credentials("ordered-user")] == [
        "binance", "coinbase", "kraken",
    ]

def test_public_user_lookup_never_returns_password_material(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "user-safe-lookup.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="safe-user", email="safe@example.test", salt=b"fixture-salt",
        password_hash=b"fixture-password-hash", created_at="2026-01-01T00:00:00+00:00",
    )

    public_user = store.get_user("safe-user")
    assert public_user == {
        "id": "safe-user",
        "email": "safe@example.test",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    assert "salt" not in public_user
    assert "password_hash" not in public_user


def test_missing_user_lookup_returns_none_without_disclosing_records(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "missing-user-safe-lookup.sqlite3"))
    store.initialize()

    assert store.get_user("does-not-exist") is None



def test_email_uniqueness_is_case_insensitive_even_for_direct_database_writes(tmp_path):
    import sqlite3

    backend = SQLiteBackend(tmp_path / "email-case-unique.sqlite3")
    store = ControlPlaneStore(backend)
    store.initialize()

    # Simulate legacy/imported data bypassing the store's lowercasing boundary.
    with backend.transaction() as session:
        session.execute(
            "INSERT INTO control_api_users(id,email,salt,password_hash,created_at) VALUES(?,?,?,?,?)",
            ("legacy-user", "MixedCase@example.test", b"salt", b"hash", "2026-01-01T00:00:00+00:00"),
        )

    with pytest.raises(sqlite3.IntegrityError):
        store.create_user(
            user_id="duplicate-user", email="mixedcase@example.test", salt=b"salt2",
            password_hash=b"hash2", created_at="2026-01-02T00:00:00+00:00",
        )

    assert store.get_user("duplicate-user") is None
    assert store.get_user_by_email("MIXEDCASE@example.test")["id"] == "legacy-user"


def test_postgres_migration_enforces_case_insensitive_email_uniqueness():
    from pathlib import Path

    migration = Path(__file__).parents[1] / "migrations" / "postgres" / "003_control_api_schema.sql"
    sql = migration.read_text(encoding="utf-8").lower()
    assert "create unique index if not exists control_api_users_email_ci_idx" in sql
    assert "on control_api_users(lower(email))" in sql


def test_engine_session_registry_persists_owner_and_scopes_listing(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "engine-sessions.sqlite3"))
    store.initialize()
    for user_id, email in (("engine-owner-a", "engine-a@example.test"),
                           ("engine-owner-b", "engine-b@example.test")):
        store.create_user(user_id=user_id, email=email, salt=b"salt", password_hash=b"hash",
                          created_at="2026-10-19T00:00:00+00:00")
    store.register_engine_session(
        session_id="paper-session-a", owner_user_id="engine-owner-a", mode="paper",
        phase="RUNNING", created_at="2026-10-19T01:00:00+00:00",
        updated_at="2026-10-19T01:00:00+00:00", metadata_json='{"source":"test"}',
    )
    store.register_engine_session(
        session_id="paper-session-b", owner_user_id="engine-owner-b", mode="simulation",
        phase="STOPPED", created_at="2026-10-19T02:00:00+00:00",
        updated_at="2026-10-19T02:00:00+00:00",
    )

    assert store.get_engine_session("paper-session-a")["owner_user_id"] == "engine-owner-a"
    assert [row["session_id"] for row in store.list_engine_sessions("engine-owner-a")] == ["paper-session-a"]
    assert store.update_engine_session(
        session_id="paper-session-a", owner_user_id="engine-owner-b", phase="STOPPED",
        updated_at="2026-10-19T03:00:00+00:00",
    ) is False
    assert store.get_engine_session("paper-session-a")["phase"] == "RUNNING"
    assert store.update_engine_session(
        session_id="paper-session-a", owner_user_id="engine-owner-a", phase="STOPPED",
        updated_at="2026-10-19T04:00:00+00:00", stopped_at="2026-10-19T04:00:00+00:00",
    ) is True
    assert store.get_engine_session("paper-session-a")["stopped_at"] == "2026-10-19T04:00:00+00:00"


def test_engine_session_registry_rejects_live_mode_and_invalid_limits(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "engine-session-validation.sqlite3"))
    store.initialize()
    store.create_user(user_id="engine-user", email="engine-user@example.test", salt=b"salt",
                      password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00")

    with pytest.raises(ValueError):
        store.register_engine_session(
            session_id="live-session", owner_user_id="engine-user", mode="live", phase="RUNNING",
            created_at="2026-10-19T00:00:00+00:00", updated_at="2026-10-19T00:00:00+00:00",
        )
    with pytest.raises(ValueError):
        store.list_engine_sessions("engine-user", limit=0)


def test_engine_session_postgres_migration_has_owner_fk_and_non_live_constraint():
    from pathlib import Path
    migration = Path(__file__).parents[1] / "migrations" / "postgres" / "004_engine_session_registry.sql"
    sql = migration.read_text(encoding="utf-8").lower()
    assert "owner_user_id text not null references control_api_users(id) on delete cascade" in sql
    assert "check (mode in ('paper', 'simulation'))" in sql
    assert "control_api_engine_sessions_owner_created_idx" in sql


def test_deleting_engine_session_owner_cascades_registry_row(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "engine-session-cascade.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="session-owner", email="session-owner@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )
    store.register_engine_session(
        session_id="owned-paper-session", owner_user_id="session-owner", mode="paper",
        phase="STOPPED", created_at="2026-10-19T00:00:00+00:00",
        updated_at="2026-10-19T00:00:00+00:00",
    )

    with store.backend.transaction() as session:
        session.execute("DELETE FROM control_api_users WHERE id=?", ("session-owner",))

    assert store.get_engine_session("owned-paper-session") is None


def test_engine_session_registry_rejects_unknown_phases_and_terminal_restart(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "engine-session-state-machine.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="state-machine-owner", email="state-machine@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )

    with pytest.raises(ValueError, match="phase"):
        store.register_engine_session(
            session_id="invalid-phase-session", owner_user_id="state-machine-owner",
            mode="paper", phase="RUNNING_BUT_UNSAFE",
            created_at="2026-10-19T00:00:00+00:00",
            updated_at="2026-10-19T00:00:00+00:00",
        )

    store.register_engine_session(
        session_id="terminal-session", owner_user_id="state-machine-owner",
        mode="paper", phase="STOPPED",
        created_at="2026-10-19T00:00:00+00:00",
        updated_at="2026-10-19T00:00:00+00:00",
    )
    with pytest.raises(ValueError, match="transition"):
        store.update_engine_session(
            session_id="terminal-session", owner_user_id="state-machine-owner",
            phase="RUNNING", updated_at="2026-10-19T01:00:00+00:00",
        )
    assert store.get_engine_session("terminal-session")["phase"] == "STOPPED"


def test_engine_session_registry_allows_idempotent_phase_update_and_failure_terminal(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "engine-session-failure-state.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="failure-owner", email="failure-owner@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )
    store.register_engine_session(
        session_id="failure-session", owner_user_id="failure-owner",
        mode="simulation", phase="RUNNING",
        created_at="2026-10-19T00:00:00+00:00",
        updated_at="2026-10-19T00:00:00+00:00",
    )
    assert store.update_engine_session(
        session_id="failure-session", owner_user_id="failure-owner",
        phase="RUNNING", updated_at="2026-10-19T00:30:00+00:00",
    ) is True
    assert store.update_engine_session(
        session_id="failure-session", owner_user_id="failure-owner",
        phase="FAILED", updated_at="2026-10-19T01:00:00+00:00",
        error_code="WORKER_FAILED",
    ) is True
    with pytest.raises(ValueError, match="transition"):
        store.update_engine_session(
            session_id="failure-session", owner_user_id="failure-owner",
            phase="RUNNING", updated_at="2026-10-19T02:00:00+00:00",
        )


def test_terminal_session_timestamp_is_stable_on_idempotent_update(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "terminal-timestamp.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="terminal-time-owner", email="terminal-time@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )
    store.register_engine_session(
        session_id="terminal-time-session", owner_user_id="terminal-time-owner",
        mode="paper", phase="RUNNING", created_at="2026-10-19T00:00:00+00:00",
        updated_at="2026-10-19T00:00:00+00:00",
    )
    store.update_engine_session(
        session_id="terminal-time-session", owner_user_id="terminal-time-owner",
        phase="STOPPED", updated_at="2026-10-19T01:00:00+00:00",
    )
    first = store.get_engine_session("terminal-time-session")["stopped_at"]
    store.update_engine_session(
        session_id="terminal-time-session", owner_user_id="terminal-time-owner",
        phase="STOPPED", updated_at="2026-10-19T02:00:00+00:00",
    )
    assert store.get_engine_session("terminal-time-session")["stopped_at"] == first


def test_engine_session_terminal_timestamp_cannot_be_set_early_or_overwritten(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "terminal-timestamp-immutable.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="immutable-time-owner", email="immutable-time@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )
    store.register_engine_session(
        session_id="immutable-time-session", owner_user_id="immutable-time-owner",
        mode="paper", phase="RUNNING", created_at="2026-10-19T00:00:00+00:00",
        updated_at="2026-10-19T00:00:00+00:00",
    )

    # Caller-supplied terminal time must not be recorded before a terminal phase.
    store.update_engine_session(
        session_id="immutable-time-session", owner_user_id="immutable-time-owner",
        phase="STOPPING", updated_at="2026-10-19T00:30:00+00:00",
        stopped_at="2026-10-19T00:15:00+00:00",
    )
    assert store.get_engine_session("immutable-time-session")["stopped_at"] is None

    store.update_engine_session(
        session_id="immutable-time-session", owner_user_id="immutable-time-owner",
        phase="STOPPED", updated_at="2026-10-19T01:00:00+00:00",
    )
    first = store.get_engine_session("immutable-time-session")["stopped_at"]
    assert first == "2026-10-19T01:00:00+00:00"

    # Idempotent terminal updates cannot rewrite the original stop time.
    store.update_engine_session(
        session_id="immutable-time-session", owner_user_id="immutable-time-owner",
        phase="STOPPED", updated_at="2026-10-19T02:00:00+00:00",
        stopped_at="2026-10-19T01:30:00+00:00",
    )
    assert store.get_engine_session("immutable-time-session")["stopped_at"] == first


def test_engine_session_idempotent_update_preserves_terminal_error_code(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "terminal-error-code.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="terminal-error-owner", email="terminal-error@example.test",
        salt=b"salt", password_hash=b"hash",
        created_at="2026-10-19T00:00:00+00:00",
    )
    store.register_engine_session(
        session_id="terminal-error-session", owner_user_id="terminal-error-owner",
        mode="paper", phase="RUNNING",
        created_at="2026-10-19T00:00:00+00:00",
        updated_at="2026-10-19T00:00:00+00:00",
    )
    store.update_engine_session(
        session_id="terminal-error-session", owner_user_id="terminal-error-owner",
        phase="FAILED", updated_at="2026-10-19T01:00:00+00:00",
        error_code="WORKER_FAILED",
    )

    # An idempotent update must not erase diagnostic evidence accidentally.
    store.update_engine_session(
        session_id="terminal-error-session", owner_user_id="terminal-error-owner",
        phase="FAILED", updated_at="2026-10-19T02:00:00+00:00",
    )
    row = store.get_engine_session("terminal-error-session")
    assert row["error_code"] == "WORKER_FAILED"
    assert row["stopped_at"] == "2026-10-19T01:00:00+00:00"





def test_worker_lease_is_exclusive_renewable_and_fenced(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "worker-lease.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="lease-owner", email="lease-owner@example.test",
        salt=b"salt", password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
    )
    store.register_engine_session(
        session_id="lease-session", owner_user_id="lease-owner", mode="paper",
        phase="CREATED", created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    first = store.claim_engine_worker_lease(
        session_id="lease-session", worker_id="worker-a", now_epoch=100, lease_seconds=30,
    )
    assert first == {
        "session_id": "lease-session", "worker_id": "worker-a",
        "lease_expires_at": 130, "fencing_token": 1,
    }
    renewed = store.claim_engine_worker_lease(
        session_id="lease-session", worker_id="worker-a", now_epoch=110, lease_seconds=30,
    )
    assert renewed["lease_expires_at"] == 140
    assert renewed["fencing_token"] == 1
    assert store.claim_engine_worker_lease(
        session_id="lease-session", worker_id="worker-b", now_epoch=120, lease_seconds=30,
    ) is None

    takeover = store.claim_engine_worker_lease(
        session_id="lease-session", worker_id="worker-b", now_epoch=140, lease_seconds=30,
    )
    assert takeover["fencing_token"] == 2
    assert not store.release_engine_worker_lease(
        session_id="lease-session", worker_id="worker-a", fencing_token=1,
    )
    assert store.release_engine_worker_lease(
        session_id="lease-session", worker_id="worker-b", fencing_token=2,
    )


def test_terminal_engine_session_cannot_acquire_worker_lease(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "terminal-worker-lease.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="terminal-owner", email="terminal-owner@example.test",
        salt=b"salt", password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
    )
    store.register_engine_session(
        session_id="terminal-session", owner_user_id="terminal-owner", mode="simulation",
        phase="CREATED", created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    assert store.update_engine_session(
        session_id="terminal-session", owner_user_id="terminal-owner",
        phase="STOPPED", updated_at="2026-01-01T00:01:00+00:00",
    )
    assert store.claim_engine_worker_lease(
        session_id="terminal-session", worker_id="worker-a", now_epoch=200, lease_seconds=30,
    ) is None



def test_terminal_transition_releases_worker_lease_and_invalidates_old_owner(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "terminal-lease-release.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="lease-stop-owner", email="lease-stop-owner@example.test",
        salt=b"salt", password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
    )
    store.register_engine_session(
        session_id="lease-stop-session", owner_user_id="lease-stop-owner", mode="paper",
        phase="CREATED", created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    lease = store.claim_engine_worker_lease(
        session_id="lease-stop-session", worker_id="worker-stop", now_epoch=100, lease_seconds=30,
    )
    assert lease is not None
    assert store.update_engine_session(
        session_id="lease-stop-session", owner_user_id="lease-stop-owner",
        phase="STOPPED", updated_at="2026-01-01T00:01:00+00:00",
    )
    assert store.release_engine_worker_lease(
        session_id="lease-stop-session", worker_id="worker-stop",
        fencing_token=lease["fencing_token"],
    ) is False
    assert store.claim_engine_worker_lease(
        session_id="lease-stop-session", worker_id="worker-new", now_epoch=200, lease_seconds=30,
    ) is None
