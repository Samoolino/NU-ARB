"""Backend-neutral storage primitives.

This is an opt-in foundation, not a runtime cutover: existing API and journal
call sites remain SQLite-backed until their query/schema parity is migrated.
"""
from __future__ import annotations
import os, sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

@runtime_checkable
class StorageSession(Protocol):
    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> Any: ...
    def fetch_one(self, statement: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None: ...
    def fetch_all(self, statement: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]: ...

@runtime_checkable
class StorageBackend(Protocol):
    dialect: str
    def transaction(self) -> Any: ...
    def health_check(self) -> bool: ...

class _SQLiteSession:
    def __init__(self, connection: sqlite3.Connection): self._connection = connection
    def execute(self, statement, parameters=()): return self._connection.execute(statement, parameters)
    def fetch_one(self, statement, parameters=()):
        row = self._connection.execute(statement, parameters).fetchone()
        return dict(row) if row is not None else None
    def fetch_all(self, statement, parameters=()):
        return [dict(row) for row in self._connection.execute(statement, parameters).fetchall()]

class SQLiteBackend:
    dialect = "sqlite"
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
    @contextmanager
    def transaction(self) -> Iterator[_SQLiteSession]:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            with connection: yield _SQLiteSession(connection)
        finally: connection.close()
    def health_check(self) -> bool:
        try:
            with self.transaction() as session:
                row = session.fetch_one("SELECT 1 AS healthy")
                return bool(row and row["healthy"] == 1)
        except (sqlite3.Error, OSError): return False

class _PostgresSession:
    def __init__(self, connection): self._connection = connection
    def execute(self, statement, parameters=()): return self._connection.execute(statement, parameters)
    def fetch_one(self, statement, parameters=()):
        row = self._connection.execute(statement, parameters).fetchone()
        return dict(row) if row is not None else None
    def fetch_all(self, statement, parameters=()):
        return [dict(row) for row in self._connection.execute(statement, parameters).fetchall()]

class PostgresBackend:
    """Psycopg 3 backend. SQL statements must use the selected dialect syntax."""
    dialect = "postgres"
    def __init__(self, database_url: str):
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("PostgreSQL URL must use postgresql:// or postgres://")
        self.database_url = database_url
    @contextmanager
    def transaction(self) -> Iterator[_PostgresSession]:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self.database_url, connect_timeout=5, row_factory=dict_row) as connection:
            yield _PostgresSession(connection)
    def health_check(self) -> bool:
        try:
            with self.transaction() as session:
                row = session.fetch_one("SELECT 1 AS healthy")
                return bool(row and row["healthy"] == 1)
        except Exception: return False

def backend_from_environment() -> StorageBackend:
    kind = os.getenv("ARBX_STORAGE_BACKEND", "sqlite").strip().lower()
    if kind == "sqlite": return SQLiteBackend(os.getenv("ARBX_APP_DB", "arbx_app.sqlite3"))
    if kind in {"postgres", "postgresql"}:
        url = os.getenv("ARBX_DATABASE_URL", "")
        if not url: raise ValueError("ARBX_DATABASE_URL is required for PostgreSQL storage")
        return PostgresBackend(url)
    raise ValueError("ARBX_STORAGE_BACKEND must be sqlite or postgres")
