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
