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
