from __future__ import annotations

import pytest

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
