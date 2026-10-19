from __future__ import annotations

from fastapi.testclient import TestClient

from arbx import web_api


def test_liveness_probe_does_not_require_database(monkeypatch):
    def fail_connect():
        raise AssertionError("liveness must not query persistence")
    monkeypatch.setattr(web_api, "_connect", fail_connect)

    response = TestClient(web_api.app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_probe_reports_current_sqlite_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(web_api, "APP_DB", tmp_path / "api.sqlite3")

    response = TestClient(web_api.app).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "storage": "sqlite"}


def test_readiness_probe_returns_sanitized_503_when_storage_unavailable(monkeypatch):
    def fail_connect():
        import sqlite3
        raise sqlite3.OperationalError("private database path details")
    monkeypatch.setattr(web_api, "_connect", fail_connect)

    response = TestClient(web_api.app).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Control API persistence is not ready"}
    assert "private database path details" not in response.text


def test_readiness_probe_checks_selected_postgres_backend_without_sqlite_fallback(monkeypatch):
    class HealthyPostgresBackend:
        dialect = "postgres"
        def health_check(self):
            return True

    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("ARBX_DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setattr(web_api, "control_api_backend_from_environment", lambda: HealthyPostgresBackend())

    response = TestClient(web_api.app).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "storage": "postgres"}


def test_orchestrator_healthz_checks_selected_postgres_backend(monkeypatch):
    class HealthyPostgresBackend:
        dialect = "postgres"
        def health_check(self):
            return True

    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("ARBX_DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("ENGINE_PROXY_TOKEN", "test-proxy-token")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "11" * 32)
    monkeypatch.setattr(web_api, "control_api_backend_from_environment", lambda: HealthyPostgresBackend())

    response = TestClient(web_api.app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "storage": "postgres"}


def test_orchestrator_healthz_fails_closed_when_selected_postgres_is_unhealthy(monkeypatch):
    class UnhealthyPostgresBackend:
        dialect = "postgres"
        def health_check(self):
            return False

    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("ARBX_DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("ENGINE_PROXY_TOKEN", "test-proxy-token")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "11" * 32)
    monkeypatch.setattr(web_api, "control_api_backend_from_environment", lambda: UnhealthyPostgresBackend())

    response = TestClient(web_api.app).get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"detail": "Control API persistence is not ready"}
