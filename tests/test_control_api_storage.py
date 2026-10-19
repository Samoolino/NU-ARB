from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arbx import web_api


def test_control_api_storage_factory_defaults_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("ARBX_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("ARBX_DATABASE_URL", raising=False)
    monkeypatch.setattr(web_api, "APP_DB", tmp_path / "control.sqlite3")

    backend = web_api.control_api_backend_from_environment()

    assert backend.dialect == "sqlite"
    assert backend.health_check() is True


def test_control_api_postgres_selection_requires_database_url(monkeypatch):
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.delenv("ARBX_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="ARBX_DATABASE_URL"):
        web_api.control_api_backend_from_environment()


def test_control_api_postgres_backend_is_explicit_and_not_silently_sqlite(monkeypatch):
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "ARBX_DATABASE_URL",
        "postgresql://nu_arb_test:nu_arb_test_only@localhost:5432/nu_arb_test",
    )

    backend = web_api.control_api_backend_from_environment()

    assert backend.dialect == "postgres"


def test_control_api_routes_fail_closed_for_postgres_without_creating_sqlite_db(tmp_path, monkeypatch):
    database = tmp_path / "must-not-be-created.sqlite3"
    monkeypatch.setattr(web_api, "APP_DB", database)
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "ARBX_DATABASE_URL",
        "postgresql://nu_arb_test:nu_arb_test_only@localhost:5432/nu_arb_test",
    )

    with pytest.raises(web_api.HTTPException) as error:
        web_api._connect()

    assert error.value.status_code == 503
    assert "not yet supported" in error.value.detail
    assert not database.exists()


def test_control_plane_store_dependency_does_not_initialize_schema_implicitly(tmp_path, monkeypatch):
    monkeypatch.delenv("ARBX_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("ARBX_DATABASE_URL", raising=False)
    monkeypatch.setattr(web_api, "APP_DB", tmp_path / "not-initialized.sqlite3")

    store = web_api.get_control_plane_store()

    assert store.backend.dialect == "sqlite"
    assert not (tmp_path / "not-initialized.sqlite3").exists()



def test_signup_uses_injected_store_when_postgres_is_selected(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import SQLiteBackend

    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "ARBX_DATABASE_URL",
        "postgresql://nu_arb_test:nu_arb_test_only@localhost:5432/nu_arb_test",
    )
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "injected-control.sqlite3"))
    store.initialize()
    web_api.app.dependency_overrides[web_api.get_control_plane_store] = lambda: store
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            response = client.post(
                "/api/v1/auth/signup",
                json={
                    "email": "injected@example.test",
                    "password": "a-long-test-password",
                    "password_confirmation": "a-long-test-password",
                },
            )
        assert response.status_code == 200, response.text
        assert response.json()["user"]["email"] == "injected@example.test"
        assert store.get_user_by_email("injected@example.test") is not None
    finally:
        web_api.app.dependency_overrides.pop(web_api.get_control_plane_store, None)
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)


def test_duplicate_case_insensitive_signup_returns_conflict_with_injected_store(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import SQLiteBackend

    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "ARBX_DATABASE_URL",
        "postgresql://nu_arb_test:nu_arb_test_only@localhost:5432/nu_arb_test",
    )
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "injected-control.sqlite3"))
    store.initialize()
    web_api.app.dependency_overrides[web_api.get_control_plane_store] = lambda: store
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            first = client.post(
                "/api/v1/auth/signup",
                json={
                    "email": "duplicate@example.test",
                    "password": "a-long-test-password",
                    "password_confirmation": "a-long-test-password",
                },
            )
            second = client.post(
                "/api/v1/auth/signup",
                json={
                    "email": "DUPLICATE@example.test",
                    "password": "another-long-password",
                    "password_confirmation": "another-long-password",
                },
            )
        assert first.status_code == 200, first.text
        assert second.status_code == 409, second.text
        assert store.get_user_by_email("duplicate@example.test") is not None
    finally:
        web_api.app.dependency_overrides.pop(web_api.get_control_plane_store, None)
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)



def test_injected_store_auth_login_me_and_logout_round_trip(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import SQLiteBackend

    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "ARBX_DATABASE_URL",
        "postgresql://nu_arb_test:nu_arb_test_only@localhost:5432/nu_arb_test",
    )
    monkeypatch.setenv("APP_SECURE_COOKIE", "0")
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "auth-roundtrip.sqlite3"))
    store.initialize()
    web_api.app.dependency_overrides[web_api.get_control_plane_store] = lambda: store
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            signup = client.post(
                "/api/v1/auth/signup",
                json={
                    "email": "roundtrip@example.test",
                    "password": "a-long-test-password",
                    "password_confirmation": "a-long-test-password",
                },
            )
            assert signup.status_code == 200, signup.text
            raw_token = client.cookies.get(web_api.SESSION_COOKIE)
            assert raw_token
            token_hash = web_api._session_token_hash(raw_token)
            assert store.get_session(token_hash) is not None
            assert store.get_session(raw_token) is None

            me = client.get("/api/v1/auth/me")
            assert me.status_code == 200, me.text
            assert me.json()["user"]["email"] == "roundtrip@example.test"

            logout = client.post("/api/v1/auth/logout")
            assert logout.status_code == 200, logout.text
            assert store.get_session(token_hash) is None

            unauthorized = client.get("/api/v1/auth/me")
            assert unauthorized.status_code == 401

            bad_login = client.post(
                "/api/v1/auth/login",
                json={"email": "roundtrip@example.test", "password": "wrong-password"},
            )
            assert bad_login.status_code == 401
            good_login = client.post(
                "/api/v1/auth/login",
                json={"email": "ROUNDTRIP@example.test", "password": "a-long-test-password"},
            )
            assert good_login.status_code == 200, good_login.text
            assert client.get("/api/v1/auth/me").status_code == 200
    finally:
        web_api.app.dependency_overrides.pop(web_api.get_control_plane_store, None)
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)


def test_expired_injected_store_session_is_rejected(tmp_path, monkeypatch):
    import hashlib
    import secrets
    import time
    from fastapi.testclient import TestClient
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import SQLiteBackend

    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "ARBX_DATABASE_URL",
        "postgresql://nu_arb_test:nu_arb_test_only@localhost:5432/nu_arb_test",
    )
    monkeypatch.setenv("APP_SECURE_COOKIE", "0")
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "expired-session.sqlite3"))
    store.initialize()
    user_id = "expired-session-user"
    store.create_user(
        user_id=user_id,
        email="expired@example.test",
        salt=b"0123456789abcdef",
        password_hash=b"not-used-in-this-test",
        created_at="2026-01-01T00:00:00+00:00",
    )
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    store.create_session(token_hash=token_hash, user_id=user_id, expires_at=int(time.time()) - 1)
    web_api.app.dependency_overrides[web_api.get_control_plane_store] = lambda: store
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            client.cookies.set(web_api.SESSION_COOKIE, raw_token)
            response = client.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert store.get_session(token_hash) is None
    finally:
        web_api.app.dependency_overrides.pop(web_api.get_control_plane_store, None)
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)



def test_exchange_list_uses_injected_store_with_postgres_selected(tmp_path, monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import SQLiteBackend

    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("ARBX_DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "exchange-list.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="exchange-list-user", email="list@example.test",
        salt=b"0123456789abcdef", password_hash=b"hash",
        created_at="2026-01-01T00:00:00+00:00",
    )
    store.save_credentials(
        user_id="exchange-list-user", exchange_id="binance",
        encrypted_credentials=b"encrypted", auth_mode="api_key",
        state="ACCOUNT_DATA_CONNECTED", last_verified="2026-10-18T00:00:00+00:00",
        verification_json=json.dumps({"evidence": {"scannerEligible": True},
                                      "balances": {"USDT": {"free": 4, "used": 1}},
                                      "orderBook": None, "maskedKey": "abc••••xyz"}),
    )
    web_api.app.dependency_overrides[web_api.get_control_plane_store] = lambda: store
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            client.cookies.set(web_api.SESSION_COOKIE, "opaque-test-token")
            store.create_session(
                token_hash=web_api._session_token_hash("opaque-test-token"),
                user_id="exchange-list-user", expires_at=2_000_000_000,
            )
            response = client.get("/api/v1/exchanges")
        assert response.status_code == 200, response.text
        binance = next(item for item in response.json()["exchanges"] if item["id"] == "binance")
        assert binance["state"] in ("ACCOUNT_DATA_CONNECTED", "STALE")
        assert binance["maskedKey"] == "abc••••xyz"
        assert binance["balances"]["USDT"]["free"] == 4
    finally:
        web_api.app.dependency_overrides.pop(web_api.get_control_plane_store, None)
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)


def test_remove_exchange_deletes_only_current_users_store_credentials(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import SQLiteBackend

    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("ARBX_DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "exchange-remove.sqlite3"))
    store.initialize()
    for user_id, email in (("owner-a", "a@example.test"), ("owner-b", "b@example.test")):
        store.create_user(user_id=user_id, email=email, salt=b"0123456789abcdef",
                          password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00")
    store.save_credentials(user_id="owner-a", exchange_id="binance", encrypted_credentials=b"a",
                          auth_mode="api_key", state="FAILED", last_verified=None, verification_json="{}")
    store.save_credentials(user_id="owner-b", exchange_id="binance", encrypted_credentials=b"b",
                          auth_mode="api_key", state="FAILED", last_verified=None, verification_json="{}")
    web_api.app.dependency_overrides[web_api.get_control_plane_store] = lambda: store
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            client.cookies.set(web_api.SESSION_COOKIE, "owner-a-token")
            store.create_session(token_hash=web_api._session_token_hash("owner-a-token"),
                                 user_id="owner-a", expires_at=2_000_000_000)
            response = client.delete("/api/v1/exchanges/binance")
        assert response.status_code == 200, response.text
        assert store.get_credentials("owner-a", "binance") is None
        assert store.get_credentials("owner-b", "binance") is not None
    finally:
        web_api.app.dependency_overrides.pop(web_api.get_control_plane_store, None)
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)



def test_default_sqlite_signup_remains_compatible_with_legacy_engine_auth(tmp_path, monkeypatch):
    import sqlite3
    from fastapi.testclient import TestClient

    database = tmp_path / "legacy-auth.sqlite3"
    monkeypatch.setattr(web_api, "APP_DB", database)
    monkeypatch.delenv("ARBX_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("ARBX_DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_SECURE_COOKIE", "0")
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            response = client.post(
                "/api/v1/auth/signup",
                json={
                    "email": "legacy@example.test",
                    "password": "a-long-test-password",
                    "password_confirmation": "a-long-test-password",
                },
            )
            assert response.status_code == 200, response.text
            me_response = client.get("/api/v1/auth/me")
            assert me_response.status_code == 200, me_response.text
            assert me_response.json()["user"]["email"] == "legacy@example.test"
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT COUNT(*) FROM users WHERE email=?",
                              ("legacy@example.test",)).fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    finally:
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)


def test_sqlite_login_normalizes_whitespace_around_email(tmp_path, monkeypatch):
    """SQLite and PostgreSQL auth routes must accept the same normalized email."""
    database = tmp_path / "sqlite-login-normalization.sqlite3"
    monkeypatch.setattr(web_api, "APP_DB", database)
    monkeypatch.delenv("ARBX_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("ARBX_DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_SECURE_COOKIE", "0")
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            signup = client.post(
                "/api/v1/auth/signup",
                json={
                    "email": "login-normalization@example.test",
                    "password": "a-long-test-password",
                    "password_confirmation": "a-long-test-password",
                },
            )
            assert signup.status_code == 200, signup.text
            client.cookies.clear()

            login = client.post(
                "/api/v1/auth/login",
                json={
                    "email": "  login-normalization@example.test  ",
                    "password": "a-long-test-password",
                },
            )
            assert login.status_code == 200, login.text
            me_response = client.get("/api/v1/auth/me")
            assert me_response.status_code == 200, me_response.text
            assert me_response.json()["user"]["email"] == "login-normalization@example.test"
    finally:
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)


def test_engine_journal_route_fails_closed_for_postgres_without_sqlite_fallback(tmp_path, monkeypatch):
    """Unmigrated journal reads must not silently return legacy SQLite data."""
    database = tmp_path / "legacy-engine-journal.sqlite3"
    monkeypatch.setattr(web_api, "APP_DB", database)
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "ARBX_DATABASE_URL",
        "postgresql://nu_arb_test:nu_arb_test_only@localhost:5432/nu_arb_test",
    )
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            response = client.get("/api/v1/engine/journal")

        assert response.status_code == 503, response.text
        assert "not yet supported" in response.json()["detail"]
        assert not database.exists()
        assert not (tmp_path / "engine_test-user.sqlite3").exists()
    finally:
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/v1/engine/state", None),
        ("post", "/api/v1/engine/stop", None),
        (
            "post",
            "/api/v1/engine/start",
            {
                "mode": "paper",
                "exchange_ids": ["binance"],
                "trade_size_usd": 1,
                "max_loss_usd": 1,
                "target_profit_usd": 1,
                "require_private_stream": True,
            },
        ),
    ],
)
def test_engine_lifecycle_routes_fail_closed_for_postgres_without_sqlite_fallback(
    tmp_path, monkeypatch, method, path, payload
):
    """Lifecycle routes must not initialize or read SQLite when PostgreSQL is selected."""
    database = tmp_path / "legacy-engine-state.sqlite3"
    monkeypatch.setattr(web_api, "APP_DB", database)
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "ARBX_DATABASE_URL",
        "postgresql://nu_arb_test:nu_arb_test_only@localhost:5432/nu_arb_test",
    )
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            response = getattr(client, method)(path, json=payload) if payload else getattr(client, method)(path)

        assert response.status_code == 503, response.text
        assert "not yet supported" in response.json()["detail"]
        assert not database.exists()
        assert not (tmp_path / "engine_test-user.sqlite3").exists()
    finally:
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)


def test_postgres_readiness_stays_closed_until_control_api_parity_is_complete(tmp_path, monkeypatch):
    """A healthy PostgreSQL connection is insufficient while route parity is unfinished."""
    from fastapi.testclient import TestClient

    database = tmp_path / "must-not-be-created.sqlite3"
    monkeypatch.setattr(web_api, "APP_DB", database)
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "ARBX_DATABASE_URL",
        "postgresql://nu_arb_test:nu_arb_test_only@localhost:5432/nu_arb_test",
    )

    class HealthyPostgresBackend:
        dialect = "postgres"

        def health_check(self):
            return True

    monkeypatch.setattr(web_api, "control_api_backend_from_environment", lambda: HealthyPostgresBackend())
    monkeypatch.setattr(web_api, "POSTGRES_CONTROL_API_PARITY_COMPLETE", False)

    with TestClient(web_api.app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503, response.text
    assert "route parity is not complete" in response.json()["detail"]
    assert not database.exists()


def test_signup_store_rolls_back_user_when_initial_session_insert_fails(tmp_path):
    """Account creation must be atomic: no orphaned user if session persistence fails."""
    import sqlite3
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import SQLiteBackend

    store = ControlPlaneStore(SQLiteBackend(tmp_path / "signup-atomicity.sqlite3"))
    store.initialize()
    with store.backend.transaction() as db:
        db.execute(
            """CREATE TRIGGER reject_initial_session
               BEFORE INSERT ON control_api_sessions
               BEGIN SELECT RAISE(ABORT, 'injected session write failure'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError):
        store.create_user_with_session(
            user_id="atomic-user",
            email="atomic@example.test",
            salt=b"0123456789abcdef",
            password_hash=b"test-password-hash",
            created_at="2026-10-19T00:00:00+00:00",
            token_hash="hashed-session-token",
            expires_at=2_000_000_000,
        )

    assert store.get_user_by_email("atomic@example.test") is None
    assert store.get_session("hashed-session-token") is None


def test_live_engine_start_is_disabled_even_when_environment_flag_is_set(monkeypatch):
    monkeypatch.setenv("ARBX_LIVE_TRADING_ENABLED", "1")
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            response = client.post(
                "/api/v1/engine/start",
                json={
                    "mode": "live",
                    "exchange_ids": ["binance", "bybit"],
                    "trade_size_usd": 1,
                    "max_loss_usd": 1,
                    "target_profit_usd": 10,
                    "require_private_stream": True,
                    "cross_live": True,
                    "live_confirmation": "I ACCEPT REAL ORDERS",
                },
            )
        assert response.status_code == 503, response.text
        assert "disabled in this build" in response.json()["detail"]
    finally:
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)


def test_runtime_never_advertises_live_execution_as_enabled(monkeypatch):
    monkeypatch.setenv("ARBX_LIVE_TRADING_ENABLED", "1")
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            runtime = client.get("/api/v1/runtime")
            health = client.get("/api/v1/health")
        assert runtime.status_code == 200, runtime.text
        assert runtime.json()["liveTradingEnabled"] is False
        assert runtime.json()["executionEnabled"] is False
        assert health.status_code == 200, health.text
        assert health.json()["execution_enabled"] is False
    finally:
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)


def test_postgres_engine_journal_reads_only_sessions_owned_by_authenticated_user(tmp_path, monkeypatch):
    """Journal reads must resolve the cookie owner to only their session IDs."""
    from fastapi.testclient import TestClient
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import SQLiteBackend

    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("ARBX_DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setattr(web_api, "POSTGRES_CONTROL_API_PARITY_COMPLETE", True)
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "journal-owner-scope.sqlite3"))
    store.initialize()
    for user_id, email in (("journal-owner-a", "journal-a@example.test"),
                           ("journal-owner-b", "journal-b@example.test")):
        store.create_user(user_id=user_id, email=email, salt=b"0123456789abcdef",
                          password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00")
    store.create_session(token_hash=web_api._session_token_hash("journal-owner-a-token"),
                         user_id="journal-owner-a", expires_at=2_000_000_000)
    store.register_engine_session(session_id="owned-session-a", owner_user_id="journal-owner-a",
                                  mode="paper", phase="STOPPED", created_at="2026-10-18T00:00:00Z",
                                  updated_at="2026-10-18T00:00:00Z")
    store.register_engine_session(session_id="foreign-session-b", owner_user_id="journal-owner-b",
                                  mode="simulation", phase="STOPPED", created_at="2026-10-18T00:00:00Z",
                                  updated_at="2026-10-18T00:00:00Z")

    class FakeJournal:
        requested_sessions = []
        def __init__(self, backend):
            assert backend.dialect == "sqlite"
        def recent_trades(self, session_id, limit=100):
            self.requested_sessions.append(session_id)
            return [{"id": 1, "session_id": session_id, "timestamp": "2026-10-18T00:00:00Z", "net_pnl": 3.5}]
        def realized(self, session_id):
            return 3.5 if session_id == "owned-session-a" else 999.0
        def close(self):
            return None

    import arbx.journal_storage
    import arbx.storage
    monkeypatch.setattr(arbx.journal_storage, "BackendTradeJournal", FakeJournal)
    monkeypatch.setattr(arbx.storage, "backend_from_environment",
                        lambda: SQLiteBackend(tmp_path / "journal-data.sqlite3"))
    web_api.app.dependency_overrides[web_api.get_control_plane_store] = lambda: store
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            client.cookies.set(web_api.SESSION_COOKIE, "journal-owner-a-token")
            response = client.get("/api/v1/engine/journal")
        assert response.status_code == 200, response.text
        body = response.json()
        assert [trade["session_id"] for trade in body["trades"]] == ["owned-session-a"]
        assert body["realizedNetPnl"] == 3.5
        assert body["scope"] == "owned durable engine sessions"
        assert FakeJournal.requested_sessions == ["owned-session-a"]
    finally:
        web_api.app.dependency_overrides.pop(web_api.get_control_plane_store, None)
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)

def test_postgres_engine_state_returns_latest_owned_durable_session(tmp_path, monkeypatch):
    """State reads the authenticated user's durable session, never another owner's."""
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import SQLiteBackend

    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("ARBX_DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setattr(web_api, "POSTGRES_CONTROL_API_PARITY_COMPLETE", True)
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "engine-state-owner-scope.sqlite3"))
    store.initialize()
    for user_id, email in (("state-owner-a", "state-a@example.test"),
                           ("state-owner-b", "state-b@example.test")):
        store.create_user(user_id=user_id, email=email, salt=b"0123456789abcdef",
                          password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00")
    store.create_session(token_hash=web_api._session_token_hash("state-owner-a-token"),
                         user_id="state-owner-a", expires_at=2_000_000_000)
    store.register_engine_session(session_id="owned-old", owner_user_id="state-owner-a", mode="paper",
                                  phase="STOPPED", created_at="2026-10-17T00:00:00Z",
                                  updated_at="2026-10-17T00:00:00Z")
    store.register_engine_session(session_id="owned-latest", owner_user_id="state-owner-a", mode="simulation",
                                  phase="RUNNING", created_at="2026-10-18T00:00:00Z",
                                  updated_at="2026-10-18T00:00:00Z")
    store.register_engine_session(session_id="foreign-session", owner_user_id="state-owner-b", mode="paper",
                                  phase="RUNNING", created_at="2026-10-19T00:00:00Z",
                                  updated_at="2026-10-19T00:00:00Z")
    web_api.app.dependency_overrides[web_api.get_control_plane_store] = lambda: store
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            client.cookies.set(web_api.SESSION_COOKIE, "state-owner-a-token")
            response = client.get("/api/v1/engine/state")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["sessionId"] == "owned-latest"
        assert body["status"] == "RUNNING"
        assert body["mode"] == "simulation"
        assert body["executionEnabled"] is False
        assert body["stats"] is None
    finally:
        web_api.app.dependency_overrides.pop(web_api.get_control_plane_store, None)
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)

@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/v1/engine/start", {
            "mode": "paper", "exchange_ids": ["binance"], "trade_size_usd": 1,
            "max_loss_usd": 1, "target_profit_usd": 1, "require_private_stream": True,
        }),
        ("post", "/api/v1/engine/stop", None),
    ],
)
def test_postgres_engine_mutations_remain_disabled_even_if_read_parity_flag_is_set(
    tmp_path, monkeypatch, method, path, payload
):
    """Read-route parity must never accidentally enable process-local engine mutations."""
    database = tmp_path / "must-not-be-created.sqlite3"
    monkeypatch.setattr(web_api, "APP_DB", database)
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "ARBX_DATABASE_URL",
        "postgresql://nu_arb_test:nu_arb_test_only@localhost:5432/nu_arb_test",
    )
    monkeypatch.setattr(web_api, "POSTGRES_CONTROL_API_PARITY_COMPLETE", True)
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            response = getattr(client, method)(path, json=payload) if payload else getattr(client, method)(path)

        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "PostgreSQL engine lifecycle runtime is not yet supported"
        assert not database.exists()
    finally:
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)
