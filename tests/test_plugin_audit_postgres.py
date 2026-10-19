from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from arbx.plugins.audit import PostgresPluginAuditWriter


class FakeSession:
    def __init__(self):
        self.calls = []

    def execute(self, statement, parameters=()):
        self.calls.append((statement, parameters))
        return None


class FakeBackend:
    dialect = "postgres"

    def __init__(self):
        self.session = FakeSession()
        self.transaction_count = 0

    class _Tx:
        def __init__(self, owner):
            self.owner = owner

        def __enter__(self):
            self.owner.transaction_count += 1
            return self.owner.session

        def __exit__(self, *args):
            return False

    def transaction(self):
        return self._Tx(self)


def test_postgres_audit_writer_persists_metadata_without_raw_payload():
    backend = FakeBackend()
    writer = PostgresPluginAuditWriter(backend)
    record = {
        "provider": "docs",
        "operation": "lookup",
        "request_id": "request-123",
        "scope": "evidence:read",
        "outcome": "completed",
        "latency_ms": 17,
        "result_code": "ok",
        "payload_sha256": "a" * 64,
        "recorded_at": "2026-10-18T10:00:00+00:00",
    }

    asyncio.run(writer(record))

    assert backend.transaction_count == 1
    sql, params = backend.session.calls[0]
    assert "INSERT INTO plugin_audit_log" in sql
    assert params == (
        "docs", "lookup", "request-123", "evidence:read", "completed",
        17, "ok", "a" * 64, datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc),
    )
    assert "payload" not in sql.lower() or "payload_sha256" in sql.lower()


def test_postgres_audit_writer_rejects_invalid_record_before_write():
    backend = FakeBackend()
    writer = PostgresPluginAuditWriter(backend)
    invalid = {
        "provider": "docs", "operation": "lookup", "request_id": "r",
        "scope": "evidence:read", "outcome": "completed",
        "latency_ms": -1, "result_code": "ok", "payload_sha256": None,
        "recorded_at": "2026-10-18T10:00:00+00:00",
    }

    try:
        asyncio.run(writer(invalid))
    except ValueError:
        pass
    else:
        raise AssertionError("negative latency must be rejected")
    assert backend.session.calls == []
