from __future__ import annotations

from scripts.postgres_migration_rehearsal import build_rehearsal_plan


def test_plan_is_metadata_only_and_never_authorizes_writes():
    report = {
        "schema_family": "legacy_control_api",
        "safe_to_plan_cutover": True,
        "blockers": [],
        "warnings": ["structural checks only"],
        "tables": {
            "users": {"present": True, "rows": 2, "missing_columns": []},
            "sessions": {"present": True, "rows": 3, "missing_columns": []},
            "exchange_credentials": {"present": True, "rows": 1, "missing_columns": []},
        },
        "integrity_checks": {"orphan_sessions": 0, "orphan_credentials": 0},
    }

    plan = build_rehearsal_plan(report)

    assert plan["mode"] == "plan_only"
    assert plan["writes_enabled"] is False
    assert plan["cutover_approved"] is False
    assert plan["source_counts"] == {"users": 2, "sessions": 3, "exchange_credentials": 1}
    assert plan["steps"] == [
        "review schema mapping and identity collision policy",
        "provision an explicitly disposable local PostgreSQL target",
        "apply reviewed migrations to the disposable target",
        "import records in a single transaction",
        "compare counts, foreign keys, expiry values, and credential bytes",
        "inject a failure and verify transaction rollback",
        "perform a restore rehearsal and obtain release approval",
    ]


def test_blocked_preflight_produces_non_executable_plan_without_sensitive_fields():
    report = {
        "schema_family": "partial_legacy_control_api",
        "safe_to_plan_cutover": False,
        "blockers": ["duplicate emails exist after case/whitespace normalization"],
        "warnings": [],
        "tables": {"users": {"present": True, "rows": 2, "missing_columns": ["email"]}},
    }

    plan = build_rehearsal_plan(report)

    assert plan["mode"] == "blocked"
    assert plan["writes_enabled"] is False
    assert plan["cutover_approved"] is False
    assert plan["blockers"] == report["blockers"]
    assert "email" not in str(plan["source_counts"])
    assert "destination_url" not in plan
