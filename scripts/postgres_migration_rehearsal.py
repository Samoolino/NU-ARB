"""Create a metadata-only, non-executable PostgreSQL migration rehearsal plan.

This command performs no writes to either database and never connects to PostgreSQL.
It wraps the read-only SQLite preflight and emits counts, blockers, and review steps.
A plan is not migration approval.
Run: python scripts/postgres_migration_rehearsal.py --database path/to/copied.sqlite3
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from scripts.postgres_migration_preflight import inspect_database

REHEARSAL_STEPS = [
    "review schema mapping and identity collision policy",
    "provision an explicitly disposable local PostgreSQL target",
    "apply reviewed migrations to the disposable target",
    "import records in a single transaction",
    "compare counts, foreign keys, expiry values, and credential bytes",
    "inject a failure and verify transaction rollback",
    "perform a restore rehearsal and obtain release approval",
]
TABLES = ("users", "sessions", "exchange_credentials")


def build_rehearsal_plan(report: dict[str, Any]) -> dict[str, Any]:
    """Build a safe plan from preflight metadata; never include row-level values."""
    source_counts = {
        table: (
            int(report["tables"][table]["rows"])
            if report.get("tables", {}).get(table, {}).get("present")
            and report["tables"][table].get("rows") is not None
            else None
        )
        for table in TABLES
    }
    permitted = (
        report.get("safe_to_plan_cutover") is True
        and report.get("schema_family") == "legacy_control_api"
        and not report.get("blockers")
        and all(source_counts[table] is not None for table in TABLES)
    )
    plan: dict[str, Any] = {
        "mode": "plan_only" if permitted else "blocked",
        "writes_enabled": False,
        "cutover_approved": False,
        "source_schema_family": report.get("schema_family", "unknown"),
        "source_counts": source_counts,
        "blockers": list(report.get("blockers", [])),
        "warnings": list(report.get("warnings", [])),
    }
    if permitted:
        plan["steps"] = list(REHEARSAL_STEPS)
        plan["notice"] = (
            "Planning only. No destination was contacted, no records were imported, "
            "and cutover is not approved."
        )
    else:
        plan["notice"] = (
            "Blocked by source preflight. Resolve blockers and rerun before planning "
            "a disposable-target rehearsal."
        )
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", required=True,
        help="Existing SQLite application database; opened read-only",
    )
    args = parser.parse_args(argv)
    try:
        report = inspect_database(Path(args.database))
    except (OSError, sqlite3.Error) as exc:
        # Do not print exception messages because they can contain filesystem paths.
        print(json.dumps({"mode": "error", "error": type(exc).__name__}, sort_keys=True))
        return 2
    plan = build_rehearsal_plan(report)
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0 if plan["mode"] == "plan_only" else 1


if __name__ == "__main__":
    sys.exit(main())
