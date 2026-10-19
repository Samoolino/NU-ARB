from __future__ import annotations

from fastapi.testclient import TestClient

from arbx import web_api


class _HealthyPostgresBackend:
    dialect = "postgres"

    def health_check(self) -> bool:
        return True


def test_healthy_postgres_does_not_open_readiness_before_route_parity(monkeypatch):
    """Database reachability alone is not evidence that API parity is complete."""
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("ARBX_DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setattr(web_api, "POSTGRES_CONTROL_API_PARITY_COMPLETE", False)
    monkeypatch.setattr(
        web_api,
        "control_api_backend_from_environment",
        lambda: _HealthyPostgresBackend(),
    )

    with TestClient(web_api.app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "PostgreSQL Control API route parity is not complete"
    }


def test_readiness_rejects_non_postgres_backend_for_postgres_selection(monkeypatch):
    """A factory returning the wrong backend must never report PostgreSQL ready."""
    class _WrongBackend:
        dialect = "sqlite"

        def health_check(self) -> bool:
            return True

    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(web_api, "POSTGRES_CONTROL_API_PARITY_COMPLETE", True)
    monkeypatch.setattr(
        web_api,
        "control_api_backend_from_environment",
        lambda: _WrongBackend(),
    )

    with TestClient(web_api.app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Control API persistence is not ready"}
