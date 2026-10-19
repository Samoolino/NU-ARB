"""Deterministic guardrail for agent-proposed assessments.

An LLM may populate a proposal upstream, but this module validates the proposal
against engine-owned facts. It never places, cancels, or modifies orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class AgentAssessment:
    status: str
    reasons: tuple[str, ...]
    advisory_summary: str = ""


def assess_observation(
    observation: Mapping[str, Any],
    *,
    max_age_ms: int = 1500,
    min_net_edge_bps: float = 0.0,
) -> AgentAssessment:
    """Return a fail-closed *advisory* status from normalized engine observations.

    Required fields are ``age_ms``, ``net_edge_bps``, ``books_synchronized``,
    ``risk_gate_passed``, and ``venue_evidence_fresh``. This result is not live
    eligibility and cannot authorize execution.
    """
    raw_age = observation.get("age_ms")
    raw_edge = observation.get("net_edge_bps")
    if (isinstance(raw_age, bool) or isinstance(raw_edge, bool)
            or not isinstance(raw_age, (int, float))
            or not isinstance(raw_edge, (int, float))):
        return AgentAssessment("REJECTED", ("invalid_numeric_evidence",))

    age_ms = float(raw_age)
    edge_bps = float(raw_edge)
    reasons: list[str] = []
    if age_ms < 0:
        reasons.append("invalid_negative_age")
    elif age_ms > max_age_ms:
        reasons.append("stale_market_evidence")
    if edge_bps < min_net_edge_bps:
        reasons.append("net_edge_below_threshold")
    if observation.get("books_synchronized") is not True:
        reasons.append("books_not_confirmed_synchronized")
    if observation.get("risk_gate_passed") is not True:
        reasons.append("engine_risk_gate_not_passed")
    if observation.get("venue_evidence_fresh") is not True:
        reasons.append("venue_evidence_not_fresh")

    if reasons:
        return AgentAssessment("REJECTED", tuple(reasons))
    return AgentAssessment(
        "ADVISORY_PASS",
        (),
        "Observation passed advisory checks only; engine-owned execution gates remain mandatory.",
    )
