from __future__ import annotations

import pytest

from arbx.journal_storage import BackendTradeJournal
from arbx.storage import SQLiteBackend


def make_journal(tmp_path):
    backend = SQLiteBackend(tmp_path / "journal.sqlite3")
    return BackendTradeJournal(backend)


def test_journal_persists_trade_and_only_filled_rows_contribute_to_realized(tmp_path):
    journal = make_journal(tmp_path)
    try:
        journal.append({
            "session_id": "s1", "mode": "simulation", "exchange_a": "alpha",
            "strategy": "fixture", "net_pnl": 1.25, "execution_status": "FILLED",
            "verification_status": "verified", "cumulative_realized_pnl": 1.25,
            "path": ["alpha", "beta"], "symbols": ["BTC/USDT"],
        })
        journal.append({
            "session_id": "s1", "mode": "simulation", "exchange_a": "alpha",
            "strategy": "fixture", "net_pnl": 99.0, "execution_status": "REJECTED",
            "verification_status": "unverified", "cumulative_realized_pnl": 1.25,
        })
        assert journal.realized("s1") == pytest.approx(1.25)
    finally:
        journal.close()


def test_opportunity_evidence_round_trips_and_limit_is_validated(tmp_path):
    journal = make_journal(tmp_path)
    try:
        journal.append_opportunity({
            "session_id": "s1", "mode": "simulation", "strategy": "fixture",
            "exchange_a": "alpha", "symbol": "BTC/USDT", "requested_usd": 10,
            "expected_net_usd": 0.2, "worst_case_net_usd": -0.1,
            "expected_net_bps": 20, "worst_case_net_bps": -10,
            "book_age_ms": 10, "decision": "REJECT",
            "evidence": {"source": "deterministic-test"},
        })
        rows = journal.recent_opportunities()
        assert len(rows) == 1
        assert rows[0]["evidence"] == {"source": "deterministic-test"}
        with pytest.raises(ValueError):
            journal.recent_opportunities(0)
    finally:
        journal.close()


def test_certification_evidence_round_trips(tmp_path):
    journal = make_journal(tmp_path)
    try:
        journal.record_certification(
            session_id="s1", venue="fixture", symbol="BTC/USDT",
            notional_usd=10, live_eligible=False,
            reasons=["simulation_only"], evidence={"status": "unverified"},
        )
        row = journal.latest_certification("fixture")
        assert row is not None
        assert row["live_eligible"] is False
        assert row["reasons"] == ["simulation_only"]
        assert row["evidence"] == {"status": "unverified"}
    finally:
        journal.close()
