from arbx.agentic.supervisor import aggregate_reports


def report(role, finding="INFO", *, observation_id="obs-1", observed_at_ms=9900):
    return {
        "role": role,
        "finding": finding,
        "observation_id": observation_id,
        "observed_at_ms": observed_at_ms,
        "summary": "diagnostic only",
    }


def baseline():
    return [
        report("connectivity"),
        report("market_data"),
        report("risk_review"),
    ]


def test_fresh_well_formed_reports_are_advisory_only():
    result = aggregate_reports(
        baseline(), expected_observation_id="obs-1", now_ms=10000
    )
    assert result.status == "ADVISORY_ONLY"
    assert result.advisory_only is True
    assert not result.reasons


def test_reject_finding_cannot_be_outvoted():
    reports = baseline()
    reports.append(report("opportunity", "REJECT"))
    result = aggregate_reports(
        reports,
        expected_observation_id="obs-1",
        now_ms=10000,
        required_roles=("connectivity", "market_data", "risk_review"),
    )
    assert result.status == "REJECTED"
    assert "opportunity_finding_reject" in result.reasons


def test_missing_required_role_fails_closed():
    result = aggregate_reports(
        baseline()[:2], expected_observation_id="obs-1", now_ms=10000
    )
    assert result.status == "REJECTED"
    assert "required_agent_role_missing" in result.reasons


def test_stale_report_fails_closed():
    reports = baseline()
    reports[0] = report("connectivity", observed_at_ms=1)
    result = aggregate_reports(
        reports, expected_observation_id="obs-1", now_ms=10000, max_age_ms=100
    )
    assert result.status == "REJECTED"
    assert "stale_or_future_agent_report" in result.reasons


def test_wrong_observation_id_fails_closed():
    reports = baseline()
    reports[0] = report("connectivity", observation_id="different")
    result = aggregate_reports(
        reports, expected_observation_id="obs-1", now_ms=10000
    )
    assert result.status == "REJECTED"
    assert "observation_id_mismatch" in result.reasons


def test_malformed_report_fails_closed():
    reports = baseline()
    reports[0] = {**reports[0], "observed_at_ms": True}
    result = aggregate_reports(
        reports, expected_observation_id="obs-1", now_ms=10000
    )
    assert result.status == "REJECTED"
    assert "malformed_agent_report" in result.reasons


def test_unhashable_role_and_finding_fail_closed():
    for field, value in (("role", ["connectivity"]), ("finding", {"ok": True})):
        reports = baseline()
        reports[0] = {**reports[0], field: value}
        result = aggregate_reports(
            reports, expected_observation_id="obs-1", now_ms=10000
        )
        assert result.status == "REJECTED"
        assert "malformed_agent_report" in result.reasons


def test_duplicate_role_fails_closed():
    reports = baseline() + [report("connectivity")]
    result = aggregate_reports(
        reports, expected_observation_id="obs-1", now_ms=10000
    )
    assert result.status == "REJECTED"
    assert "duplicate_agent_role" in result.reasons


def test_agent_report_cannot_grant_execution_authority():
    result = aggregate_reports(
        baseline(), expected_observation_id="obs-1", now_ms=10000
    )
    assert result.status == "ADVISORY_ONLY"
    assert not hasattr(result, "live_eligible")
    assert not hasattr(result, "execute")


def test_report_collection_is_bounded():
    reports = (report("connectivity") for _ in range(1000000))
    result = aggregate_reports(
        reports, expected_observation_id="obs-1", now_ms=10000
    )
    assert result.status == "REJECTED"
    assert "too_many_agent_reports" in result.reasons
    assert result.reports == ()


def test_duplicate_required_roles_are_invalid_configuration():
    result = aggregate_reports(
        baseline(),
        expected_observation_id="obs-1",
        now_ms=10000,
        required_roles=("connectivity", "connectivity"),
    )
    assert result.status == "REJECTED"
    assert "invalid_supervisor_configuration" in result.reasons


def test_summary_control_characters_are_removed():
    reports = baseline()
    reports[0] = {**reports[0], "summary": "line1\nline2\x1b[31m"}
    result = aggregate_reports(
        reports, expected_observation_id="obs-1", now_ms=10000
    )
    assert result.status == "ADVISORY_ONLY"
    summary = next(r.summary for r in result.reports if r.role == "connectivity")
    assert "\n" not in summary
    assert "\x1b" not in summary


def test_mapping_parser_exception_fails_closed():
    class ExplodingDict(dict):
        def get(self, key, default=None):
            raise RuntimeError("untrusted mapping failed")

    reports = baseline()
    reports[0] = ExplodingDict(reports[0])
    result = aggregate_reports(
        reports, expected_observation_id="obs-1", now_ms=10000
    )
    assert result.status == "REJECTED"
    assert "malformed_agent_report" in result.reasons


def test_unicode_control_characters_are_removed():
    reports = baseline()
    reports[0] = {**reports[0], "summary": "safe\u009bunsafe\u202e"}
    result = aggregate_reports(
        reports, expected_observation_id="obs-1", now_ms=10000
    )
    assert result.status == "ADVISORY_ONLY"
    summary = next(r.summary for r in result.reports if r.role == "connectivity")
    assert "\u009b" not in summary
    assert "\u202e" not in summary


def test_oversized_observation_identifiers_fail_closed():
    reports = baseline()
    reports[0] = {**reports[0], "observation_id": "x" * 257}
    result = aggregate_reports(
        reports, expected_observation_id="obs-1", now_ms=10000
    )
    assert result.status == "REJECTED"
    assert "malformed_agent_report" in result.reasons


def test_oversized_expected_observation_identifier_fails_closed():
    result = aggregate_reports(
        baseline(), expected_observation_id="x" * 257, now_ms=10000
    )
    assert result.status == "REJECTED"
    assert "invalid_supervisor_configuration" in result.reasons
