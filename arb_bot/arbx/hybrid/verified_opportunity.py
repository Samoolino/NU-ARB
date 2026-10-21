"""Deterministic eligibility contract for a live arbitrage engagement.

This module intentionally has no exchange/network dependencies. Callers must pass
fresh observations produced by the actual venue adapters; synthetic data cannot
certify a live route.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VenueSnapshot:
    venue: str
    verified: bool
    balance_usd: float
    book_age_ms: float
    latency_ms: float
    fee_usd: float = 0.0
    slippage_reserve_usd: float = 0.0
    network_cost_usd: float = 0.0
    permission_ok: bool = False
    stream_ok: bool = False


@dataclass(frozen=True, slots=True)
class Opportunity:
    expected_gross_usd: float
    expected_fees_usd: float
    expected_slippage_usd: float
    expected_network_usd: float = 0.0
    worst_case_reserve_usd: float = 0.0
    required_capital_usd: float = 0.0
    session_realized_pnl_usd: float = 0.0
    target_profit_usd: float | None = None


@dataclass(frozen=True, slots=True)
class Eligibility:
    ok: bool
    reason: str
    expected_net_usd: float
    worst_case_net_usd: float
    deployable_capital_usd: float


def evaluate_opportunity(
    opportunity: Opportunity,
    venues: tuple[VenueSnapshot, ...],
    *,
    now_book_age_limit_ms: float,
    latency_limit_ms: float,
    min_expected_net_usd: float,
    min_worst_case_net_usd: float,
    session_capital_usd: float | None = None,
) -> Eligibility:
    """Fail closed unless each venue's balance, permissions, stream and latency are verified."""
    expected = opportunity.expected_gross_usd - (
        opportunity.expected_fees_usd + opportunity.expected_slippage_usd +
        opportunity.expected_network_usd
    )
    worst = expected - opportunity.worst_case_reserve_usd
    if not venues:
        return Eligibility(False, "no_venues", expected, worst, 0.0)
    for venue in venues:
        if not venue.verified or not venue.permission_ok or not venue.stream_ok:
            return Eligibility(False, f"venue_unverified:{venue.venue}", expected, worst, 0.0)
        if venue.balance_usd < 0:
            return Eligibility(False, f"invalid_balance:{venue.venue}", expected, worst, 0.0)
        if venue.book_age_ms < 0 or venue.book_age_ms > now_book_age_limit_ms:
            return Eligibility(False, f"stale_book:{venue.venue}", expected, worst, 0.0)
        if venue.latency_ms < 0 or venue.latency_ms > latency_limit_ms:
            return Eligibility(False, f"latency_degraded:{venue.venue}", expected, worst, 0.0)
    available = min(v.balance_usd for v in venues)
    deployable = min(available, session_capital_usd) if session_capital_usd is not None else available
    if opportunity.target_profit_usd is not None and opportunity.session_realized_pnl_usd >= opportunity.target_profit_usd:
        return Eligibility(False, "profit_target_attained", expected, worst, deployable)
    if deployable < opportunity.required_capital_usd or opportunity.required_capital_usd <= 0:
        return Eligibility(False, "insufficient_verified_capital", expected, worst, deployable)
    if expected < min_expected_net_usd:
        return Eligibility(False, "expected_net_below_floor", expected, worst, deployable)
    if worst < min_worst_case_net_usd or worst <= 0:
        return Eligibility(False, "worst_case_net_below_floor", expected, worst, deployable)
    return Eligibility(True, "verified_profitable_opportunity", expected, worst, deployable)
