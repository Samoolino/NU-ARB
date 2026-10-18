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
