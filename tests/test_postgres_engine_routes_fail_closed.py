from __future__ import annotations

from fastapi.testclient import TestClient

from arbx import web_api


def test_engine_routes_fail_closed_when_postgres_parity_is_incomplete(tmp_path, monkeypatch):
    """PostgreSQL selection must never make engine routes fall back to SQLite."""
    database = tmp_path / "must-not-be-created.sqlite3"
    monkeypatch.setattr(web_api, "APP_DB", database)
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "ARBX_DATABASE_URL",
        "postgresql://nu_arb_test:nu_arb_test_only@localhost:5432/nu_arb_test",
    )
    web_api.app.dependency_overrides[web_api._proxy_auth] = lambda: None
    try:
        with TestClient(web_api.app) as client:
            responses = [
                client.get("/api/v1/engine/state"),
                client.get("/api/v1/engine/journal"),
                client.post(
                    "/api/v1/engine/start",
                    json={
                        "mode": "paper",
                        "exchange_ids": ["binance"],
                        "trade_size_usd": 5,
                        "max_loss_usd": 1,
                        "target_profit_usd": 10,
                        "require_private_stream": True,
                        "cross_live": False,
                    },
                ),
                client.post("/api/v1/engine/stop"),
            ]

        assert [response.status_code for response in responses] == [503, 503, 503, 503]
        assert all(
            response.json()["detail"]
            == "Configured persistence backend is not yet supported by the Control API"
            for response in responses
        )
        assert not database.exists()
    finally:
        web_api.app.dependency_overrides.pop(web_api._proxy_auth, None)
