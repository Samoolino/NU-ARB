"""Explicit PostgreSQL migration foundation; SQLite remains the runtime default."""
from __future__ import annotations

import os
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "postgres"


def _database_url(value: str | None = None) -> str:
    url = value if value is not None else os.getenv("ARBX_DATABASE_URL", "")
    if not url.strip():
        raise ValueError("ARBX_DATABASE_URL must be set to run PostgreSQL migrations")
    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("ARBX_DATABASE_URL must use postgresql:// or postgres://")
    return url


def _migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    versions = [path.name.split("_", 1)[0] for path in files]
    if len(versions) != len(set(versions)):
        raise ValueError("duplicate PostgreSQL migration version")
    return files


def run_postgres_migrations(
    database_url: str | None = None,
    directory: Path = MIGRATIONS_DIR,
) -> list[str]:
    """Apply pending trusted SQL migrations transactionally; requires psycopg 3."""
    import psycopg
    from psycopg.rows import dict_row

    url = _database_url(database_url)
    applied_now: list[str] = []
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version TEXT PRIMARY KEY,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )"""
        )
        already = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for path in _migration_files(directory):
            version = path.name.split("_", 1)[0]
            if version in already:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
            applied_now.append(path.name)
    return applied_now
