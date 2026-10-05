"""Speed-aware execution admission.

The engine treats execution time as a risk budget: a quoted edge must survive
expected completion latency plus observed short-horizon volatility. This is a
fail-closed margin test, not a profit guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpeedAssessment:
    estimated_completion_ms: float
    volatility_bps_s: float
    expected_margin_decay_bps: float
    protected_worst_bps: float
    speed_headroom_bps: float
    admissible: bool


def assess_execution_speed(
    *,
    worst_bps: float,
    volatility_bps_s: float,
    book_age_ms: float,
    buy_p95_ms: float,
    sell_p95_ms: float,
    safety_buffer_bps: float,
) -> SpeedAssessment:
    # Cross orders are submitted concurrently. Completion is dominated by the
    # slower venue, while the current book age is already consuming the edge.
    completion_ms = max(0.0, book_age_ms) + max(0.0, buy_p95_ms, sell_p95_ms)
    decay = max(0.0, volatility_bps_s) * completion_ms / 1000.0
    protected = float(worst_bps) - decay - max(0.0, safety_buffer_bps)
    return SpeedAssessment(
        estimated_completion_ms=completion_ms,
        volatility_bps_s=max(0.0, volatility_bps_s),
        expected_margin_decay_bps=decay,
        protected_worst_bps=protected,
        speed_headroom_bps=protected,
        admissible=protected > 0.0,
    )
