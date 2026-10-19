"""Read-only preflight for planning the SQLite-to-PostgreSQL control-plane migration.

This tool never writes to the source database or connects to PostgreSQL. It reports
counts and structural blockers only; it deliberately never emits emails, IDs,
password material, session token hashes, credential ciphertext, or verification JSON.
Run: python scripts/postgres_migration_preflight.py --database path/to/arbx_app.sqlite3
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

EXPECTED_COLUMNS = {
    "users": {"id", "email", "salt", "password_hash", "created_at"},
    "sessions": {"token_hash", "user_id", "expires_at"},
    "exchange_credentials": {
        "user_id", "exchange_id", "encrypted_credentials", "auth_mode",
        "state", "last_verified", "verification_json",
    },
}
REQUIRED_VALUES = {
    "users": ("id", "email", "salt", "password_hash", "created_at"),
    "sessions": ("token_hash", "user_id", "expires_at"),
    "exchange_credentials": (
        "user_id", "exchange_id", "encrypted_credentials", "auth_mode",
        "state", "verification_json",
    ),
}


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    # Table names are selected from a fixed internal constant, never user input.
    return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}


def inspect_database(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("source database file does not exist")
    uri = source.as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    blockers: list[str] = []
    warnings: list[str] = []
    report: dict[str, Any] = {
        "source": {"file_exists": True, "opened_read_only": True},
        "tables": {}, "blockers": blockers, "warnings": warnings,
        "safe_to_plan_cutover": False,
    }
    try:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            blockers.append("SQLite integrity check reported structural errors")
        report["source"]["sqlite_integrity_check"] = "ok" if integrity == "ok" else "failed"

        tables = _tables(db)
        # Current API uses legacy names. The control_api_* tables are an isolated
        # foundation and must not be mistaken for the active route data source.
        legacy_present = set(EXPECTED_COLUMNS).intersection(tables)
        control_present = {"control_api_users", "control_api_sessions", "control_api_exchange_credentials"}.intersection(tables)
        actual_columns: dict[str, set[str]] = {}
        for table in EXPECTED_COLUMNS:
            if table in tables:
                actual_columns[table] = _columns(db, table)
        complete_legacy = (
            legacy_present == set(EXPECTED_COLUMNS)
            and all(EXPECTED_COLUMNS[table].issubset(actual_columns[table]) for table in EXPECTED_COLUMNS)
        )
        report["schema_family"] = (
            "legacy_control_api" if complete_legacy
            else "partial_legacy_control_api" if legacy_present
            else "control_api_foundation_only" if control_present
            else "unrecognized_or_empty"
        )
        if not complete_legacy:
            blockers.append("active legacy control-plane schema is incomplete or absent")
        for table, required in EXPECTED_COLUMNS.items():
            if table not in tables:
                report["tables"][table] = {"present": False, "rows": None}
                continue
            actual = actual_columns[table]
            missing = sorted(required - actual)
            count = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            report["tables"][table] = {"present": True, "rows": count, "missing_columns": missing}
            if missing:
                blockers.append(f"{table} is missing required columns")

        if complete_legacy:
            duplicate_emails = db.execute(
                "SELECT COUNT(*) FROM (SELECT lower(trim(email)) AS normalized "
                "FROM users GROUP BY lower(trim(email)) HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            if duplicate_emails:
                blockers.append("duplicate emails exist after case/whitespace normalization")
            orphan_sessions = db.execute(
                "SELECT COUNT(*) FROM sessions s LEFT JOIN users u ON u.id=s.user_id WHERE u.id IS NULL"
            ).fetchone()[0]
            orphan_credentials = db.execute(
                "SELECT COUNT(*) FROM exchange_credentials c LEFT JOIN users u ON u.id=c.user_id WHERE u.id IS NULL"
            ).fetchone()[0]
            if orphan_sessions:
                blockers.append("sessions contain user references that do not resolve")
            if orphan_credentials:
                blockers.append("credentials contain user references that do not resolve")
            invalid_verification_json = db.execute(
                "SELECT COUNT(*) FROM exchange_credentials WHERE verification_json IS NULL "
                "OR json_valid(verification_json)=0"
            ).fetchone()[0]
            if invalid_verification_json:
                blockers.append("credential verification metadata contains invalid JSON")
            empty_ciphertext = db.execute(
                "SELECT COUNT(*) FROM exchange_credentials "
                "WHERE encrypted_credentials IS NULL OR length(encrypted_credentials)=0"
            ).fetchone()[0]
            if empty_ciphertext:
                blockers.append("credential rows contain empty encrypted payloads")
            null_required_values = {}
            for table, columns in REQUIRED_VALUES.items():
                for column in columns:
                    # Table/column names are constants defined above.
                    count = db.execute(
                        f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NULL'
                    ).fetchone()[0]
                    if count:
                        null_required_values[f"{table}.{column}"] = count
            if null_required_values:
                blockers.append("required fields contain NULL values")
            report["integrity_checks"] = {
                "duplicate_normalized_emails": duplicate_emails,
                "orphan_sessions": orphan_sessions,
                "orphan_credentials": orphan_credentials,
                "invalid_verification_json_rows": invalid_verification_json,
                "empty_encrypted_payload_rows": empty_ciphertext,
                "null_required_values": null_required_values,
            }
            if not blockers:
                warnings.append("preflight checks are structural only; they do not validate encryption keys, decrypt ciphertext, or prove restore readiness")
        report["safe_to_plan_cutover"] = not blockers and report["schema_family"] == "legacy_control_api"
        return report
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Existing SQLite application database; opened read-only")
    args = parser.parse_args(argv)
    try:
        report = inspect_database(args.database)
    except (OSError, sqlite3.Error) as exc:
        # Exception messages can disclose source paths or filesystem details.
        print(json.dumps({"error": type(exc).__name__}, indent=2))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["safe_to_plan_cutover"] else 1


if __name__ == "__main__":
    sys.exit(main())
