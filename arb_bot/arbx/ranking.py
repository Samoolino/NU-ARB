"""Opportunity ranking for target-progress execution.

The score is a deterministic prioritization heuristic, not a profit guarantee.
Only opportunities already passing the risk/profit gate may be selected for execution.
"""
from __future__ import annotations

from dataclasses import dataclass


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class TargetRank:
    score: float
    execution_confidence: float
    expected_target_progress: float


def rank_target_progress(
    *,
    expected_net_usd: float,
    worst_case_net_usd: float,
    age_ms: float,
    max_book_age_ms: float,
    capital_utilization: float,
    target_remaining_usd: float | None,
) -> TargetRank:
    """Rank an already-gated opportunity by expected verified target progress.

    Confidence intentionally discounts stale books, weak modeled floors, and
    highly concentrated capital usage. This is a prioritization signal only;
    execution, capital reconciliation, and realized PnL remain authoritative.
    """
    expected = max(0.0, float(expected_net_usd))
    worst = float(worst_case_net_usd)
    age = max(0.0, float(age_ms))
    max_age = max(1.0, float(max_book_age_ms))
    utilization = _clamp(float(capital_utilization))

    floor_confidence = _clamp(worst / expected) if expected > 0.0 else 0.0
    freshness_confidence = _clamp(1.0 - age / max_age)
    utilization_confidence = 1.0 - 0.5 * utilization
    confidence = floor_confidence * freshness_confidence * utilization_confidence

    remaining = float(target_remaining_usd) if target_remaining_usd is not None else 0.0
    if remaining > 0.0:
        progress = expected * confidence / remaining
    else:
        progress = expected * confidence

    return TargetRank(
        score=progress,
        execution_confidence=confidence,
        expected_target_progress=progress,
    )
