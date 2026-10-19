"""Deterministic paper-replay regressions; no exchange network or credentials."""
from __future__ import annotations

import time

from arbx.hybrid.depth import normalize_depth, validate_depth
from arbx.hybrid.execution import ExecutionCoordinator, ExecutionState
from arbx.journal import TradeJournal


# Small recorded fixture: exchange timestamps/sequences are retained for audit.
RECORDED_BOOK_EVENTS = (
    {
        "venue": "venue-a",
        "symbol": "BTC/USDT",
        "timestamp": 1_792_281_600_000,
        "nonce": 101,
        "bids": [[100.0, 2.0], [99.9, 2.0], [99.8, 2.0]],
        "asks": [[100.1, 2.0], [100.2, 2.0], [100.3, 2.0]],
    },
)


def test_recorded_book_event_is_accepted_when_fresh_and_rejected_when_stale():
    event = RECORDED_BOOK_EVENTS[0]
    fresh = normalize_depth(
        event["venue"], event["symbol"], event, recv=time.monotonic()
    )
    assert fresh.sequence == 101
    assert validate_depth(fresh, notional_usd=100.0, max_age_ms=1_000).ok

    stale = normalize_depth(
        event["venue"], event["symbol"], event, recv=time.monotonic() - 10.0
    )
    result = validate_depth(stale, notional_usd=100.0, max_age_ms=1_000)
    assert not result.ok
    assert result.reason == "stale_depth"


def test_partial_fill_requires_reconciliation_and_only_verified_fill_counts(tmp_path):
    coordinator = ExecutionCoordinator()
    assert coordinator.on_fill(1.0, 0.4, order_id="paper-1") == ExecutionState.PARTIALLY_FILLED
    coordinator.require_reconciliation()
    assert coordinator.state == ExecutionState.RECONCILING

    path = tmp_path / "paper-replay.sqlite3"
    journal = TradeJournal(path)
    journal.append({
        "session_id": "replay-1",
        "mode": "paper",
        "exchange_a": "venue-a",
        "strategy": "cross_exchange",
        "requested_quantity": 1.0,
        "executed_quantity": 0.4,
        "net_pnl": 0.5,
        "execution_status": "PARTIALLY_FILLED",
        "verification_status": "PENDING_RECONCILIATION",
        "cumulative_realized_pnl": 0.0,
    })
    assert journal.realized("replay-1") == 0.0

    # Simulate reconciliation against the authoritative final order/fill record.
    journal.append({
        "session_id": "replay-1",
        "mode": "paper",
        "exchange_a": "venue-a",
        "strategy": "cross_exchange",
        "requested_quantity": 1.0,
        "executed_quantity": 0.4,
        "net_pnl": 0.12,
        "execution_status": "FILLED",
        "verification_status": "RECONCILED",
        "cumulative_realized_pnl": 0.12,
    })
    journal.close()

    reopened = TradeJournal(path)
    try:
        assert reopened.realized("replay-1") == 0.12
    finally:
        reopened.close()



def test_unverified_filled_status_does_not_count_as_realized_pnl(tmp_path):
    path = tmp_path / "unverified.sqlite3"
    journal = TradeJournal(path)
    journal.append({
        "session_id": "uncertain-1",
        "mode": "live",
        "exchange_a": "venue-a",
        "strategy": "cross_exchange",
        "requested_quantity": 1.0,
        "executed_quantity": 1.0,
        "net_pnl": 9.0,
        "execution_status": "FILLED",
        "verification_status": "PENDING_RECONCILIATION",
        "cumulative_realized_pnl": 9.0,
    })
    try:
        assert journal.realized("uncertain-1") == 0.0
    finally:
        journal.close()
