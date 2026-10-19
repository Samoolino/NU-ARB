"""Deterministic guardrail for agent-proposed assessments.

An LLM may populate a proposal upstream, but this module validates the proposal
against engine-owned facts. It never places, cancels, or modifies orders.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class AgentAssessment:
    status: str
    reasons: tuple[str, ...]
    advisory_summary: str = ""


def _finite_float(value: int | float) -> float | None:
    """Convert numeric input safely; hostile-sized integers must fail closed."""
    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def assess_observation(
    observation: Mapping[str, Any],
    *,
    max_age_ms: int = 1500,
    min_net_edge_bps: float = 0.0,
) -> AgentAssessment:
    """Return a fail-closed advisory status from normalized engine observations.

    Required fields: age_ms, net_edge_bps, books_synchronized,
    risk_gate_passed, and venue_evidence_fresh. This result is not live
    eligibility and cannot authorize execution.
    """
    if not isinstance(observation, Mapping):
        return AgentAssessment("REJECTED", ("invalid_observation_shape",))

    numeric_fields = ("age_ms", "net_edge_bps")
    if any(key not in observation for key in numeric_fields):
        return AgentAssessment("REJECTED", ("required_numeric_evidence_missing",))

    raw_age = observation["age_ms"]
    raw_edge = observation["net_edge_bps"]
    if (
        isinstance(raw_age, bool)
        or isinstance(raw_edge, bool)
        or not isinstance(raw_age, (int, float))
        or not isinstance(raw_edge, (int, float))
    ):
        return AgentAssessment("REJECTED", ("invalid_numeric_evidence",))

    age_ms = _finite_float(raw_age)
    edge_bps = _finite_float(raw_edge)
    if age_ms is None or edge_bps is None:
        return AgentAssessment("REJECTED", ("invalid_numeric_evidence",))

    if (
        isinstance(max_age_ms, bool)
        or not isinstance(max_age_ms, (int, float))
        or isinstance(min_net_edge_bps, bool)
        or not isinstance(min_net_edge_bps, (int, float))
    ):
        return AgentAssessment("REJECTED", ("invalid_threshold_configuration",))

    max_age = _finite_float(max_age_ms)
    min_edge = _finite_float(min_net_edge_bps)
    if max_age is None or min_edge is None or max_age < 0:
        return AgentAssessment("REJECTED", ("invalid_threshold_configuration",))

    reasons: list[str] = []
    if age_ms < 0:
        reasons.append("invalid_negative_age")
    elif age_ms > max_age:
        reasons.append("stale_market_evidence")
    if edge_bps < min_edge:
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
