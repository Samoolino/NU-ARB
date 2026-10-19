"""Fail-closed aggregation for advisory agent reports.

This module deliberately does not call models, exchange clients, or order APIs.
It accepts already-produced reports and returns advisory diagnostics only.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
import unicodedata
from typing import Any, Iterable, Mapping


_ALLOWED_ROLES = {
    "connectivity",
    "market_data",
    "opportunity",
    "risk_review",
    "operator_summary",
}
_ALLOWED_FINDINGS = {"INFO", "WARN", "REJECT", "UNKNOWN"}
_MAX_SUMMARY_CHARS = 500
_MAX_OBSERVATION_ID_CHARS = 256
_MAX_REPORTS = 20


@dataclass(frozen=True, slots=True)
class AgentReport:
    role: str
    finding: str
    observation_id: str
    observed_at_ms: int
    summary: str


@dataclass(frozen=True, slots=True)
class SupervisorAssessment:
    status: str
    reasons: tuple[str, ...]
    reports: tuple[AgentReport, ...]
    advisory_only: bool = True


def _parse_report(value: Mapping[str, Any]) -> AgentReport | None:
    """Parse an untrusted agent payload; malformed payloads are never trusted."""
    role = value.get("role")
    finding = value.get("finding")
    observation_id = value.get("observation_id")
    observed_at_ms = value.get("observed_at_ms")
    summary = value.get("summary")

    # Type-check before set membership: JSON arrays/objects are unhashable.
    if not isinstance(role, str) or role not in _ALLOWED_ROLES:
        return None
    if not isinstance(finding, str) or finding not in _ALLOWED_FINDINGS:
        return None
    if (
        not isinstance(observation_id, str)
        or not observation_id.strip()
        or len(observation_id) > _MAX_OBSERVATION_ID_CHARS
    ):
        return None
    if (
        isinstance(observed_at_ms, bool)
        or not isinstance(observed_at_ms, int)
        or observed_at_ms < 0
    ):
        return None
    if not isinstance(summary, str) or len(summary) > _MAX_SUMMARY_CHARS:
        return None
    # Keep diagnostics to one line and remove terminal/control characters.
    safe_summary = "".join(
        " " if char == "\t" else char
        for char in summary
        if char != "\t" and unicodedata.category(char) != "Cc"
    ).strip()
    return AgentReport(role, finding, observation_id, observed_at_ms, safe_summary)


def aggregate_reports(
    reports: Iterable[Mapping[str, Any]],
    *,
    expected_observation_id: str,
    now_ms: int,
    max_age_ms: int = 1500,
    required_roles: tuple[str, ...] = ("connectivity", "market_data", "risk_review"),
) -> SupervisorAssessment:
    """Validate bounded advisory reports and fail closed on uncertainty.

    A successful result means only that the advisory reports are well-formed
    and fresh. It is never execution authorization or a replacement for
    engine-owned permission, PnL, route, balance, and risk gates.
    """
    if (
        not isinstance(expected_observation_id, str)
        or not expected_observation_id.strip()
        or len(expected_observation_id) > _MAX_OBSERVATION_ID_CHARS
        or isinstance(now_ms, bool)
        or not isinstance(now_ms, int)
        or now_ms < 0
        or isinstance(max_age_ms, bool)
        or not isinstance(max_age_ms, int)
        or max_age_ms < 0
        or not isinstance(required_roles, (tuple, list))
        or any(
            not isinstance(role, str) or role not in _ALLOWED_ROLES
            for role in required_roles
        )
        or len(set(required_roles)) != len(required_roles)
    ):
        return SupervisorAssessment("REJECTED", ("invalid_supervisor_configuration",), ())

    parsed: list[AgentReport] = []
    reasons: list[str] = []
    seen_roles: set[str] = set()
    try:
        # Bound iteration before materialization: generators may be infinite or
        # adversarially large, and must not be allowed to exhaust memory.
        incoming = list(islice(iter(reports), _MAX_REPORTS + 1))
    except Exception:
        return SupervisorAssessment("REJECTED", ("invalid_report_collection",), ())

    if len(incoming) > _MAX_REPORTS:
        return SupervisorAssessment("REJECTED", ("too_many_agent_reports",), ())

    for raw in incoming:
        if not isinstance(raw, Mapping):
            reasons.append("malformed_agent_report")
            continue
        try:
            report = _parse_report(raw)
        except Exception:
            # Treat hostile/custom Mapping implementations as malformed input.
            report = None
        if report is None:
            reasons.append("malformed_agent_report")
            continue
        if report.role in seen_roles:
            reasons.append("duplicate_agent_role")
            continue
        seen_roles.add(report.role)
        if report.observation_id != expected_observation_id:
            reasons.append("observation_id_mismatch")
            continue
        age = now_ms - report.observed_at_ms
        if age < 0 or age > max_age_ms:
            reasons.append("stale_or_future_agent_report")
            continue
        parsed.append(report)
        if report.finding in {"REJECT", "UNKNOWN"}:
            reasons.append(f"{report.role}_finding_{report.finding.lower()}")

    missing_roles = set(required_roles) - seen_roles
    if missing_roles:
        reasons.append("required_agent_role_missing")
    if reasons:
        return SupervisorAssessment("REJECTED", tuple(sorted(set(reasons))), tuple(parsed))
    return SupervisorAssessment("ADVISORY_ONLY", (), tuple(parsed))
