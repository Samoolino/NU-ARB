"""Explicit, local-only SQLite to PostgreSQL control-plane import rehearsal.

Default mode is dry-run and never opens a PostgreSQL connection. Write mode is
limited to a validated local disposable target and imports inside one transaction.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import psycopg

from scripts.postgres_migration_preflight import inspect_database
from scripts.postgres_rehearsal_target import validate_disposable_target

TABLES = ("users", "sessions", "exchange_credentials")


def _read_source(path: str | Path) -> dict[str, list[tuple[Any, ...]]]:
    source = Path(path).expanduser().resolve()
    uri = source.as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        db.execute("PRAGMA query_only = ON")
        return {
            "users": db.execute(
                "SELECT id,email,salt,password_hash,created_at FROM users ORDER BY id"
            ).fetchall(),
            "sessions": db.execute(
                "SELECT token_hash,user_id,expires_at FROM sessions ORDER BY token_hash"
            ).fetchall(),
            "exchange_credentials": db.execute(
                "SELECT user_id,exchange_id,encrypted_credentials,auth_mode,state,last_verified,verification_json "
                "FROM exchange_credentials ORDER BY user_id,exchange_id"
            ).fetchall(),
        }
    finally:
        db.close()


def _counts(rows: dict[str, list[tuple[Any, ...]]]) -> dict[str, int]:
    return {table: len(rows[table]) for table in TABLES}


def import_control_plane(
    *,
    source_path: str,
    target_url: str,
    execute: bool = False,
    production_url: str | None = None,
    fail_after_inserts: int | None = None,
) -> dict[str, Any]:
    """Plan or import legacy control-plane rows without exposing row-level values.

    `fail_after_inserts` is an internal failure-injection seam for tests; the CLI
    does not expose it. A failure inside the transaction must roll back all rows.
    """
    validate_disposable_target(target_url, production_url=production_url)
    preflight = inspect_database(source_path)
    if not preflight.get("safe_to_plan_cutover"):
        raise ValueError("Source preflight is blocked; resolve structural findings first")

    rows = _read_source(source_path)
    source_counts = _counts(rows)
    if not execute:
        return {
            "mode": "dry_run",
            "writes_enabled": False,
            "cutover_approved": False,
            "source_counts": source_counts,
            "destination_contacted": False,
            "notice": "No destination connection opened; no records imported.",
        }

    inserted = 0
    with psycopg.connect(target_url) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                # Do not merge into or overwrite a destination with existing data.
                for table in ("control_api_users", "control_api_sessions", "control_api_exchange_credentials"):
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    if cur.fetchone()[0] != 0:
                        raise ValueError("Destination control-plane tables must be empty before rehearsal")

                for row in rows["users"]:
                    cur.execute(
                        "INSERT INTO control_api_users(id,email,salt,password_hash,created_at) VALUES(%s,%s,%s,%s,%s)",
                        row,
                    )
                    inserted += 1
                    if fail_after_inserts is not None and inserted >= fail_after_inserts:
                        raise RuntimeError("Injected import rehearsal failure")

                for row in rows["sessions"]:
                    cur.execute(
                        "INSERT INTO control_api_sessions(token_hash,user_id,expires_at) VALUES(%s,%s,%s)",
                        row,
                    )
                    inserted += 1
                    if fail_after_inserts is not None and inserted >= fail_after_inserts:
                        raise RuntimeError("Injected import rehearsal failure")

                for row in rows["exchange_credentials"]:
                    cur.execute(
                        "INSERT INTO control_api_exchange_credentials"
                        "(user_id,exchange_id,encrypted_credentials,auth_mode,state,last_verified,verification_json) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                        row,
                    )
                    inserted += 1
                    if fail_after_inserts is not None and inserted >= fail_after_inserts:
                        raise RuntimeError("Injected import rehearsal failure")

                destination_counts = {}
                for table, target_table in (
                    ("users", "control_api_users"),
                    ("sessions", "control_api_sessions"),
                    ("exchange_credentials", "control_api_exchange_credentials"),
                ):
                    cur.execute(f"SELECT COUNT(*) FROM {target_table}")
                    destination_counts[table] = int(cur.fetchone()[0])

                if destination_counts != source_counts:
                    raise ValueError("Source and destination row counts do not match")

    return {
        "mode": "imported_to_disposable_target",
        "writes_enabled": True,
        "cutover_approved": False,
        "source_counts": source_counts,
        "destination_counts": source_counts,
        "counts_match": True,
        "notice": "Disposable rehearsal only; review data parity and rollback evidence before any release decision.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="SQLite source database; opened read-only")
    parser.add_argument("--target-url", required=True, help="Local disposable PostgreSQL target URL")
    parser.add_argument("--production-url-env", default="NU_ARB_PRODUCTION_DATABASE_URL",
                        help="Environment variable name containing production URL for endpoint comparison")
    parser.add_argument("--execute", action="store_true",
                        help="Import into an explicitly disposable local target; default is dry-run")
    args = parser.parse_args(argv)

    import os
    production_url = os.environ.get(args.production_url_env)
    try:
        result = import_control_plane(
            source_path=args.database,
            target_url=args.target_url,
            execute=args.execute,
            production_url=production_url,
        )
    except (OSError, sqlite3.Error, ValueError, psycopg.Error, RuntimeError) as exc:
        # Never serialize connection strings, row values, or raw driver exception messages.
        print(json.dumps({"error": type(exc).__name__}, indent=2))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
