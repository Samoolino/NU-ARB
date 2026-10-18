"""Durable PostgreSQL sink for metadata-only plugin audit records."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

from arbx.storage import StorageBackend

_ALLOWED_OUTCOMES = frozenset({"completed", "denied", "timeout", "failed"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PostgresPluginAuditWriter:
    """Persist audit metadata in PostgreSQL without storing credentials or payloads.

    The supplied backend must already have migration 002 applied. Database work is
    moved off the event loop because psycopg connections are synchronous here.
    """

    def __init__(self, backend: StorageBackend):
        if backend.dialect != "postgres":
            raise ValueError("plugin audit writer requires a PostgreSQL backend")
        self._backend = backend

    async def __call__(self, record: dict[str, Any]) -> None:
        values = self._validated_values(record)
        await asyncio.to_thread(self._insert, values)

    @staticmethod
    def _validated_values(record: dict[str, Any]) -> tuple[Any, ...]:
        required = ("provider", "operation", "request_id", "outcome", "latency_ms", "result_code", "recorded_at")
        missing = [key for key in required if key not in record]
        if missing:
            raise ValueError("missing required audit fields: " + ", ".join(missing))

        for key in ("provider", "operation", "request_id", "result_code"):
            value = record[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be non-empty text")
            if len(value) > 256:
                raise ValueError(f"{key} exceeds 256 characters")

        outcome = record["outcome"]
        if outcome not in _ALLOWED_OUTCOMES:
            raise ValueError("unsupported audit outcome")

        latency = record["latency_ms"]
        if isinstance(latency, bool) or not isinstance(latency, int) or latency < 0:
            raise ValueError("latency_ms must be a non-negative integer")

        scope = record.get("scope")
        if scope is not None and (not isinstance(scope, str) or len(scope) > 128):
            raise ValueError("scope must be text of at most 128 characters")

        digest = record.get("payload_sha256")
        if digest is not None and (not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)):
            raise ValueError("payload_sha256 must be a lowercase SHA-256 hex digest")

        recorded_at = record["recorded_at"]
        if isinstance(recorded_at, str):
            try:
                recorded_at = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("recorded_at must be an ISO-8601 timestamp") from exc
        if not isinstance(recorded_at, datetime) or recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")

        return (
            record["provider"], record["operation"], record["request_id"], scope,
            outcome, latency, record["result_code"], digest, recorded_at,
        )

    def _insert(self, values: tuple[Any, ...]) -> None:
        with self._backend.transaction() as session:
            session.execute(
                """INSERT INTO plugin_audit_log
                   (provider, operation, request_id, scope, outcome, latency_ms,
                    result_code, payload_sha256, recorded_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                values,
            )
