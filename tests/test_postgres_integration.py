"""Disposable-PostgreSQL integration checks; never point this at production."""
import os
import uuid

import psycopg
import pytest

from arbx.postgres import run_postgres_migrations


@pytest.fixture(scope="module")
def database_url():
    value = os.getenv("ARBX_TEST_POSTGRES_URL")
    if not value:
        pytest.skip("set ARBX_TEST_POSTGRES_URL for disposable PostgreSQL integration tests")
    if "localhost" not in value and "127.0.0.1" not in value and "postgres" not in value:
        pytest.fail("integration tests only accept a local/disposable PostgreSQL host")
    run_postgres_migrations(value)
    return value


def test_migrations_are_idempotent(database_url):
    assert run_postgres_migrations(database_url) == []
    with psycopg.connect(database_url) as conn:
        versions = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert versions == [("001",), ("002",), ("003",), ("004",), ("005",), ("006",), ("007",)]
    with psycopg.connect(database_url) as conn:
        indexes = {
            row[0] for row in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
            ).fetchall()
        }
    assert "control_api_engine_sessions_owner_updated_idx" in indexes


def test_foreign_key_and_unique_constraints_are_enforced(database_url):
    email = f"integration-{uuid.uuid4().hex}@example.invalid"
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
            (email, "test-hash"),
        ).fetchone()
        user_id = row[0]
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s)",
                    (email, "other-hash"),
                )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.transaction():
                conn.execute(
                    """INSERT INTO exchange_credentials
                       (user_id, venue, auth_mode, encrypted_payload)
                       VALUES (%s, %s, %s, %s)""",
                    (user_id + 999999, "fake-venue", "test", b"ciphertext"),
                )


def test_transaction_rollback_leaves_no_partial_row(database_url):
    email = "rollback@example.invalid"
    with psycopg.connect(database_url) as conn:
        with pytest.raises(RuntimeError, match="force rollback"):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s)",
                    (email, "test-hash"),
                )
                raise RuntimeError("force rollback")
        count = conn.execute("SELECT count(*) FROM users WHERE email = %s", (email,)).fetchone()[0]
    assert count == 0


def test_concurrent_unique_insert_has_one_winner(database_url):
    from concurrent.futures import ThreadPoolExecutor

    email = "concurrency@example.invalid"
    with psycopg.connect(database_url) as conn:
        conn.execute("DELETE FROM users WHERE email = %s", (email,))

    def insert_if_absent(label):
        with psycopg.connect(database_url) as conn:
            result = conn.execute(
                """INSERT INTO users (email, password_hash) VALUES (%s, %s)
                   ON CONFLICT (email) DO NOTHING""",
                (email, label),
            )
            return result.rowcount

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(insert_if_absent, ("first", "second")))
    assert sum(outcomes) == 1
    with psycopg.connect(database_url) as conn:
        count = conn.execute("SELECT count(*) FROM users WHERE email = %s", (email,)).fetchone()[0]
    assert count == 1


def test_engine_session_registry_enforces_owner_and_non_live_mode(database_url):
    suffix = uuid.uuid4().hex
    owner_email = f"engine-owner-{suffix}@example.invalid"
    with psycopg.connect(database_url) as conn:
        owner_id = conn.execute(
            "INSERT INTO control_api_users (id, email, salt, password_hash, created_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (f"engine-owner-{uuid.uuid4().hex}", owner_email, b"salt", b"hash", "2026-10-19T00:00:00+00:00"),
        ).fetchone()[0]

        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute(
                    """INSERT INTO control_api_engine_sessions
                       (session_id, owner_user_id, mode, phase, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (f"forbidden-live-session-{suffix}", owner_id, "live", "RUNNING",
                     "2026-10-19T00:00:00+00:00", "2026-10-19T00:00:00+00:00"),
                )

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.transaction():
                conn.execute(
                    """INSERT INTO control_api_engine_sessions
                       (session_id, owner_user_id, mode, phase, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (f"orphan-paper-session-{suffix}", "missing-user", "paper", "STOPPED",
                     "2026-10-19T00:00:00+00:00", "2026-10-19T00:00:00+00:00"),
                )

        conn.execute(
            """INSERT INTO control_api_engine_sessions
               (session_id, owner_user_id, mode, phase, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (f"valid-paper-session-{suffix}", owner_id, "paper", "STOPPED",
             "2026-10-19T00:00:00+00:00", "2026-10-19T00:00:00+00:00"),
        )
        row = conn.execute(
            "SELECT owner_user_id, mode FROM control_api_engine_sessions WHERE session_id=%s",
            (f"valid-paper-session-{suffix}",),
        ).fetchone()
        assert row == (owner_id, "paper")


def test_control_store_engine_registry_round_trips_on_postgres(database_url):
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import PostgresBackend

    suffix = uuid.uuid4().hex
    owner_id = f"control-store-owner-{suffix}"
    other_id = f"control-store-other-{suffix}"
    store = ControlPlaneStore(PostgresBackend(database_url))
    store.create_user(
        user_id=owner_id, email=f"{owner_id}@example.invalid", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )
    store.create_user(
        user_id=other_id, email=f"{other_id}@example.invalid", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )

    session_id = f"paper-session-{suffix}"
    store.register_engine_session(
        session_id=session_id, owner_user_id=owner_id, mode="paper", phase="CREATED",
        created_at="2026-10-19T01:00:00+00:00",
        updated_at="2026-10-19T01:00:00+00:00", metadata_json='{"origin":"integration"}',
    )
    for phase, timestamp in (
        ("STARTING", "2026-10-19T01:00:01+00:00"),
        ("PREFLIGHTING", "2026-10-19T01:00:02+00:00"),
        ("RUNNING", "2026-10-19T01:00:03+00:00"),
    ):
        assert store.update_engine_session(
            session_id=session_id, owner_user_id=owner_id,
            phase=phase, updated_at=timestamp,
        )

    assert store.get_engine_session(session_id)["owner_user_id"] == owner_id
    assert [row["session_id"] for row in store.list_engine_sessions(owner_id)] == [session_id]
    assert store.list_engine_sessions(other_id) == []
    assert store.update_engine_session(
        session_id=session_id, owner_user_id=other_id, phase="STOPPED",
        updated_at="2026-10-19T02:00:00+00:00",
    ) is False
    assert store.update_engine_session(
        session_id=session_id, owner_user_id=owner_id, phase="STOPPED",
        updated_at="2026-10-19T03:00:00+00:00", stopped_at="2026-10-19T03:00:00+00:00",
    ) is True
    assert store.get_engine_session(session_id)["phase"] == "STOPPED"



def test_postgres_worker_lease_is_exclusive_and_fences_stale_workers(database_url):
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import PostgresBackend

    suffix = uuid.uuid4().hex
    owner_id = f"lease-owner-{suffix}"
    session_id = f"lease-session-{suffix}"
    store = ControlPlaneStore(PostgresBackend(database_url))
    store.create_user(
        user_id=owner_id, email=f"{owner_id}@example.invalid", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )
    store.register_engine_session(
        session_id=session_id, owner_user_id=owner_id, mode="paper", phase="CREATED",
        created_at="2026-10-19T00:00:00+00:00", updated_at="2026-10-19T00:00:00+00:00",
    )
    from concurrent.futures import ThreadPoolExecutor

    worker_a = f"worker-a-{suffix}"
    worker_b = f"worker-b-{suffix}"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(
            lambda worker: store.claim_engine_worker_lease(
                session_id=session_id, worker_id=worker, now_epoch=100, lease_seconds=30,
            ),
            (worker_a, worker_b),
        ))
    winners = [lease for lease in outcomes if lease is not None]
    assert len(winners) == 1
    assert winners[0]["fencing_token"] == 1
    first_worker = winners[0]["worker_id"]
    second_worker = worker_b if first_worker == worker_a else worker_a

    assert store.claim_engine_worker_lease(
        session_id=session_id, worker_id=second_worker, now_epoch=120, lease_seconds=30,
    ) is None
    takeover = store.claim_engine_worker_lease(
        session_id=session_id, worker_id=second_worker, now_epoch=130, lease_seconds=30,
    )
    assert takeover["fencing_token"] == 2
    assert store.release_engine_worker_lease(
        session_id=session_id, worker_id=first_worker, fencing_token=1,
    ) is False
    assert store.release_engine_worker_lease(
        session_id=session_id, worker_id=second_worker, fencing_token=2,
    ) is True


def test_postgres_engine_state_route_uses_real_database_and_owner_scope(database_url, monkeypatch):
    """Exercise the API route against disposable PostgreSQL, not an SQLite test double."""
    from fastapi.testclient import TestClient
    from arbx import web_api
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import PostgresBackend

    suffix = uuid.uuid4().hex
    owner_id = f"route-owner-{suffix}"
    foreign_id = f"route-foreign-{suffix}"
    token = f"route-token-{suffix}"
    store = ControlPlaneStore(PostgresBackend(database_url))
    for user_id in (owner_id, foreign_id):
        store.create_user(user_id=user_id, email=f"{user_id}@example.invalid", salt=b"salt",
                          password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00")
    store.create_session(token_hash=web_api._session_token_hash(token), user_id=owner_id,
                         expires_at=2_000_000_000)
    store.register_engine_session(session_id=f"owned-session-{suffix}", owner_user_id=owner_id,
                                  mode="simulation", phase="RUNNING",
                                  created_at="2026-10-18T00:00:00+00:00",
                                  updated_at="2026-10-18T00:00:00+00:00")
    store.register_engine_session(session_id=f"foreign-session-{suffix}", owner_user_id=foreign_id,
                                  mode="paper", phase="RUNNING",
                                  created_at="2026-10-19T00:00:00+00:00",
                                  updated_at="2026-10-19T00:00:00+00:00")
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("ARBX_DATABASE_URL", database_url)
    monkeypatch.setattr(web_api, "POSTGRES_CONTROL_API_PARITY_COMPLETE", True)
    web_api.app.dependency_overrides[web_api.get_control_plane_store] = lambda: store
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            client.cookies.set(web_api.SESSION_COOKIE, token)
            response = client.get("/api/v1/engine/state")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["sessionId"] == f"owned-session-{suffix}"
        assert body["mode"] == "simulation"
        assert body["status"] == "RUNNING"
        assert body["executionEnabled"] is False
    finally:
        web_api.app.dependency_overrides.pop(web_api.get_control_plane_store, None)
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)