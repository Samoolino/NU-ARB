"""Deterministic guardrail for agent-proposed assessments.

An LLM may populate a proposal upstream, but this module validates the proposal
against engine-owned facts. It never places, cancels, or modifies orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any


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

    Required fields are `age_ms`, `net_edge_bps`, `books_synchronized`, and
    `risk_gate_passed`. Missing, malformed, stale, or failed inputs are rejected.
    This is not a live eligibility decision and cannot authorize execution.
    """
    reasons: list[str] = []
    try:
        age_ms = float(observation["age_ms"])
        edge_bps = float(observation["net_edge_bps"])
    except (KeyError, TypeError, ValueError):
        return AgentAssessment("REJECTED", ("required_numeric_evidence_missing",))

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
