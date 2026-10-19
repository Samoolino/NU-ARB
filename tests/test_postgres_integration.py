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
    assert versions == [("001",), ("002",), ("003",), ("004",), ("005",), ("006",), ("007",), ("008",)]
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

def test_latest_engine_session_query_is_owner_scoped_and_update_ordered(database_url):
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import PostgresBackend

    suffix = uuid.uuid4().hex
    owner_id = f"latest-query-owner-{suffix}"
    foreign_id = f"latest-query-foreign-{suffix}"
    store = ControlPlaneStore(PostgresBackend(database_url))
    for user_id in (owner_id, foreign_id):
        store.create_user(
            user_id=user_id, email=f"{user_id}@example.invalid", salt=b"salt",
            password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
        )
    store.register_engine_session(
        session_id=f"created-latest-{suffix}", owner_user_id=owner_id, mode="paper",
        phase="STOPPED", created_at="2026-10-19T00:00:00+00:00",
        updated_at="2026-10-19T01:00:00+00:00",
    )
    store.register_engine_session(
        session_id=f"updated-latest-{suffix}", owner_user_id=owner_id, mode="simulation",
        phase="RUNNING", created_at="2026-10-18T00:00:00+00:00",
        updated_at="2026-10-19T02:00:00+00:00",
    )
    store.register_engine_session(
        session_id=f"foreign-latest-{suffix}", owner_user_id=foreign_id, mode="paper",
        phase="RUNNING", created_at="2026-10-20T00:00:00+00:00",
        updated_at="2026-10-20T00:00:00+00:00",
    )

    latest = store.get_latest_engine_session(owner_id)
    assert latest is not None
    assert latest["session_id"] == f"updated-latest-{suffix}"
    assert latest["owner_user_id"] == owner_id



def test_postgres_credential_loader_is_owner_scoped_and_decrypts_only_requested_credentials(database_url, monkeypatch):
    """Use the real PostgreSQL store to prove owner scoping and encrypted credential loading."""
    import json
    import secrets
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from arbx import web_api
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import PostgresBackend

    suffix = uuid.uuid4().hex
    owner_id = f"credential-loader-owner-{suffix}"
    foreign_id = f"credential-loader-foreign-{suffix}"
    store = ControlPlaneStore(PostgresBackend(database_url))
    for user_id in (owner_id, foreign_id):
        store.create_user(
            user_id=user_id, email=f"{user_id}@example.invalid", salt=b"salt",
            password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
        )

    key = bytes(range(32))
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key.hex())

    def encrypted(payload):
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(key).encrypt(
            nonce, json.dumps(payload, separators=(",", ":")).encode(),
            b"arbx-exchange-credentials-v1",
        )

    store.save_credentials(
        user_id=owner_id, exchange_id="binance",
        encrypted_credentials=encrypted({"apiKey": "owner-key", "secret": "owner-secret"}),
        auth_mode="hmac", state="CONNECTED", last_verified="2026-10-19T00:00:00+00:00",
        verification_json="{}",
    )
    store.save_credentials(
        user_id=foreign_id, exchange_id="binance",
        encrypted_credentials=encrypted({"apiKey": "foreign-key", "secret": "foreign-secret"}),
        auth_mode="hmac", state="CONNECTED", last_verified="2026-10-19T00:00:00+00:00",
        verification_json="{}",
    )

    loaded = web_api._load_user_credentials(store, owner_id, ["binance"])
    assert loaded == {"binance": {"apiKey": "owner-key", "secret": "owner-secret"}}
    assert "foreign-key" not in repr(loaded)
    assert web_api._load_user_credentials(store, owner_id, []) == {}
    with pytest.raises(web_api.HTTPException, match="Missing saved credentials for bybit"):
        web_api._load_user_credentials(store, owner_id, ["bybit"])



def test_postgres_lifecycle_transition_requires_current_worker_fence(database_url):
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import PostgresBackend

    suffix = uuid.uuid4().hex
    owner_id = f"fenced-route-owner-{suffix}"
    session_id = f"fenced-route-session-{suffix}"
    store = ControlPlaneStore(PostgresBackend(database_url))
    store.create_user(
        user_id=owner_id, email=f"{owner_id}@example.invalid", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )
    store.register_engine_session(
        session_id=session_id, owner_user_id=owner_id, mode="simulation", phase="CREATED",
        created_at="2026-10-19T00:00:00+00:00", updated_at="2026-10-19T00:00:00+00:00",
    )
    old = store.claim_engine_worker_lease(
        session_id=session_id, worker_id=f"worker-old-{suffix}", now_epoch=100, lease_seconds=10,
    )
    assert old is not None
    assert store.transition_engine_session_with_lease(
        session_id=session_id, owner_user_id=owner_id, worker_id=old["worker_id"],
        fencing_token=old["fencing_token"], phase="STARTING",
        updated_at="2026-10-19T00:00:01+00:00", now_epoch=101,
    )
    new_worker = f"worker-new-{suffix}"
    takeover = store.claim_engine_worker_lease(
        session_id=session_id, worker_id=new_worker, now_epoch=110, lease_seconds=10,
    )
    assert takeover is not None and takeover["fencing_token"] == 2
    assert not store.transition_engine_session_with_lease(
        session_id=session_id, owner_user_id=owner_id, worker_id=old["worker_id"],
        fencing_token=old["fencing_token"], phase="PREFLIGHTING",
        updated_at="2026-10-19T00:00:02+00:00", now_epoch=111,
    )
    assert store.transition_engine_session_with_lease(
        session_id=session_id, owner_user_id=owner_id, worker_id=new_worker,
        fencing_token=takeover["fencing_token"], phase="PREFLIGHTING",
        updated_at="2026-10-19T00:00:03+00:00", now_epoch=111,
    )


def test_postgres_journal_append_is_atomic_with_current_worker_fence(database_url):
    from arbx.control_store import ControlPlaneStore
    from arbx.journal_storage import BackendTradeJournal
    from arbx.storage import PostgresBackend

    suffix = uuid.uuid4().hex
    owner_id = f"fenced-journal-owner-{suffix}"
    session_id = f"fenced-journal-session-{suffix}"
    backend = PostgresBackend(database_url)
    store = ControlPlaneStore(backend)
    store.create_user(
        user_id=owner_id, email=f"{owner_id}@example.invalid", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )
    store.register_engine_session(
        session_id=session_id, owner_user_id=owner_id, mode="simulation", phase="CREATED",
        created_at="2026-10-19T00:00:00+00:00", updated_at="2026-10-19T00:00:00+00:00",
    )
    assert store.update_engine_session(
        session_id=session_id, owner_user_id=owner_id, phase="STARTING",
        updated_at="2026-10-19T00:00:01+00:00",
    )
    old = store.claim_engine_worker_lease(
        session_id=session_id, worker_id=f"worker-old-{suffix}", now_epoch=100, lease_seconds=5,
    )
    assert old is not None
    journal = BackendTradeJournal(backend)
    record = {
        "session_id": session_id, "mode": "simulation", "exchange_a": "fixture",
        "strategy": "integration", "net_pnl": 0.0, "execution_status": "PAPER_RECORDED",
        "verification_status": "unverified", "cumulative_realized_pnl": 0.0,
    }
    assert journal.append_fenced(
        record, owner_user_id=owner_id, worker_id=old["worker_id"],
        fencing_token=old["fencing_token"], now_epoch=101,
    ) > 0
    new_worker = f"worker-new-{suffix}"
    takeover = store.claim_engine_worker_lease(
        session_id=session_id, worker_id=new_worker, now_epoch=105, lease_seconds=10,
    )
    assert takeover is not None and takeover["fencing_token"] > old["fencing_token"]
    with pytest.raises(PermissionError):
        journal.append_fenced(
            record, owner_user_id=owner_id, worker_id=old["worker_id"],
            fencing_token=old["fencing_token"], now_epoch=106,
        )
    assert len(journal.recent_trades(session_id)) == 1


def test_postgres_fenced_opportunity_and_certification_writes(database_url):
    from arbx.control_store import ControlPlaneStore
    from arbx.journal_storage import BackendTradeJournal
    from arbx.storage import PostgresBackend

    suffix = uuid.uuid4().hex
    owner_id = f"fenced-evidence-owner-{suffix}"
    session_id = f"fenced-evidence-session-{suffix}"
    backend = PostgresBackend(database_url)
    store = ControlPlaneStore(backend)
    store.create_user(
        user_id=owner_id, email=f"{owner_id}@example.invalid", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )
    store.register_engine_session(
        session_id=session_id, owner_user_id=owner_id, mode="paper", phase="STARTING",
        created_at="2026-10-19T00:00:00+00:00", updated_at="2026-10-19T00:00:00+00:00",
    )
    old = store.claim_engine_worker_lease(
        session_id=session_id, worker_id=f"evidence-old-{suffix}", now_epoch=100, lease_seconds=5,
    )
    new = store.claim_engine_worker_lease(
        session_id=session_id, worker_id=f"evidence-new-{suffix}", now_epoch=105, lease_seconds=10,
    )
    assert old is not None and new is not None
    journal = BackendTradeJournal(backend)
    opportunity = {
        "session_id": session_id, "mode": "paper", "strategy": "fixture",
        "exchange_a": "alpha", "exchange_b": "beta", "symbol": "BTC/USDT",
        "requested_usd": 10.0, "expected_net_usd": 0.1, "worst_case_net_usd": -0.2,
        "expected_net_bps": 10.0, "worst_case_net_bps": -20.0, "book_age_ms": 5.0,
        "decision": "REJECT", "evidence": {"source": "integration"},
    }
    certification = {
        "session_id": session_id, "venue": f"venue-{suffix}", "symbol": "BTC/USDT",
        "notional_usd": 10.0, "live_eligible": False,
        "reasons": ["paper_only"], "evidence": {"status": "unverified"},
    }
    with pytest.raises(PermissionError):
        journal.append_opportunity_fenced(
            opportunity, owner_user_id=owner_id, worker_id=old["worker_id"],
            fencing_token=old["fencing_token"], now_epoch=106,
        )
    with pytest.raises(PermissionError):
        journal.record_certification_fenced(
            **certification, owner_user_id=owner_id, worker_id=old["worker_id"],
            fencing_token=old["fencing_token"], now_epoch=106,
        )
    assert journal.append_opportunity_fenced(
        opportunity, owner_user_id=owner_id, worker_id=new["worker_id"],
        fencing_token=new["fencing_token"], now_epoch=106,
    ) > 0
    assert journal.record_certification_fenced(
        **certification, owner_user_id=owner_id, worker_id=new["worker_id"],
        fencing_token=new["fencing_token"], now_epoch=106,
    ) > 0
    assert any(row["session_id"] == session_id for row in journal.recent_opportunities())
    assert journal.latest_certification(certification["venue"])["session_id"] == session_id
