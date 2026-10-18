from __future__ import annotations

import sqlite3

from scripts.postgres_migration_preflight import inspect_database


def test_partial_legacy_tables_report_blockers_without_querying_missing_columns(tmp_path):
    path = tmp_path / "partial.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE users (id TEXT, email TEXT);
        CREATE TABLE sessions (token_hash TEXT);
        CREATE TABLE exchange_credentials (user_id TEXT, exchange_id TEXT);
    """)
    db.commit()
    db.close()

    report = inspect_database(path)

    assert report["safe_to_plan_cutover"] is False
    assert report["schema_family"] == "partial_legacy_control_api"
    assert report["blockers"]
    assert all("missing_columns" in table for table in report["tables"].values() if table["present"])
