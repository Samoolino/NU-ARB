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



def test_recent_trades_are_session_scoped_and_decode_json(tmp_path):
    journal = make_journal(tmp_path)
    try:
        journal.append({
            "session_id": "owner-session-a", "mode": "simulation", "exchange_a": "alpha",
            "strategy": "fixture", "net_pnl": 1.25, "execution_status": "FILLED",
            "verification_status": "verified", "cumulative_realized_pnl": 1.25,
            "path": ["alpha", "beta"], "symbols": ["BTC/USDT"],
        })
        journal.append({
            "session_id": "owner-session-b", "mode": "simulation", "exchange_a": "beta",
            "strategy": "fixture", "net_pnl": 9.0, "execution_status": "FILLED",
            "verification_status": "verified", "cumulative_realized_pnl": 9.0,
        })

        rows = journal.recent_trades("owner-session-a")

        assert len(rows) == 1
        assert rows[0]["session_id"] == "owner-session-a"
        assert rows[0]["path"] == ["alpha", "beta"]
        assert rows[0]["symbols"] == ["BTC/USDT"]
        with pytest.raises(ValueError):
            journal.recent_trades("owner-session-a", limit=0)
        with pytest.raises(ValueError):
            journal.recent_trades("")
    finally:
        journal.close()



def test_fenced_append_rejects_stale_worker_after_lease_takeover(tmp_path):
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import SQLiteBackend

    backend = SQLiteBackend(tmp_path / "shared.sqlite3")
    control = ControlPlaneStore(backend)
    control.initialize()
    control.create_user(
        user_id="fence-owner", email="fence-owner@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
    )
    control.register_engine_session(
        session_id="fence-session", owner_user_id="fence-owner", mode="simulation",
        phase="CREATED", created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    assert control.update_engine_session(
        session_id="fence-session", owner_user_id="fence-owner", phase="STARTING",
        updated_at="2026-01-01T00:00:01+00:00",
    )
    lease_a = control.claim_engine_worker_lease(
        session_id="fence-session", worker_id="worker-a", now_epoch=100, lease_seconds=5,
    )
    assert lease_a is not None
    lease_b = control.claim_engine_worker_lease(
        session_id="fence-session", worker_id="worker-b", now_epoch=106, lease_seconds=5,
    )
    assert lease_b is not None
    assert lease_b["fencing_token"] > lease_a["fencing_token"]

    journal = BackendTradeJournal(backend)
    record = {
        "session_id": "fence-session", "mode": "simulation", "exchange_a": "fixture",
        "strategy": "test", "net_pnl": 0.0, "execution_status": "PAPER_RECORDED",
        "verification_status": "unverified", "cumulative_realized_pnl": 0.0,
    }
    with pytest.raises(PermissionError):
        journal.append_fenced(
            record, owner_user_id="fence-owner", worker_id="worker-a",
            fencing_token=lease_a["fencing_token"], now_epoch=107,
        )
    assert journal.recent_trades("fence-session") == []

    row_id = journal.append_fenced(
        record, owner_user_id="fence-owner", worker_id="worker-b",
        fencing_token=lease_b["fencing_token"], now_epoch=107,
    )
    assert row_id > 0
    assert len(journal.recent_trades("fence-session")) == 1
    journal.close()


def test_fenced_append_rejects_owner_mode_and_expired_lease(tmp_path):
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import SQLiteBackend

    backend = SQLiteBackend(tmp_path / "shared-validation.sqlite3")
    control = ControlPlaneStore(backend)
    control.initialize()
    control.create_user(
        user_id="fence-owner", email="fence-owner2@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
    )
    control.register_engine_session(
        session_id="fence-session", owner_user_id="fence-owner", mode="paper",
        phase="CREATED", created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    assert control.update_engine_session(
        session_id="fence-session", owner_user_id="fence-owner", phase="STARTING",
        updated_at="2026-01-01T00:00:01+00:00",
    )
    lease = control.claim_engine_worker_lease(
        session_id="fence-session", worker_id="worker-a", now_epoch=100, lease_seconds=5,
    )
    journal = BackendTradeJournal(backend)
    record = {
        "session_id": "fence-session", "mode": "simulation", "exchange_a": "fixture",
        "strategy": "test", "net_pnl": 0.0, "execution_status": "PAPER_RECORDED",
        "verification_status": "unverified", "cumulative_realized_pnl": 0.0,
    }
    with pytest.raises(PermissionError):
        journal.append_fenced(
            record, owner_user_id="somebody-else", worker_id="worker-a",
            fencing_token=lease["fencing_token"], now_epoch=101,
        )
    with pytest.raises(PermissionError):
        journal.append_fenced(
            record, owner_user_id="fence-owner", worker_id="worker-a",
            fencing_token=lease["fencing_token"], now_epoch=106,
        )
    with pytest.raises(ValueError):
        journal.append_fenced(
            {**record, "mode": "live"}, owner_user_id="fence-owner", worker_id="worker-a",
            fencing_token=lease["fencing_token"], now_epoch=101,
        )
    assert journal.recent_trades("fence-session") == []
    journal.close()


def test_fenced_opportunity_and_certification_writes_reject_stale_worker(tmp_path):
    from arbx.control_store import ControlPlaneStore
    from arbx.storage import SQLiteBackend

    backend = SQLiteBackend(tmp_path / "shared-evidence.sqlite3")
    control = ControlPlaneStore(backend)
    control.initialize()
    control.create_user(
        user_id="evidence-owner", email="evidence-owner@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-01-01T00:00:00+00:00",
    )
    control.register_engine_session(
        session_id="evidence-session", owner_user_id="evidence-owner", mode="simulation",
        phase="CREATED", created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    assert control.update_engine_session(
        session_id="evidence-session", owner_user_id="evidence-owner", phase="STARTING",
        updated_at="2026-01-01T00:00:01+00:00",
    )
    old = control.claim_engine_worker_lease(
        session_id="evidence-session", worker_id="evidence-worker-old",
        now_epoch=100, lease_seconds=5,
    )
    new = control.claim_engine_worker_lease(
        session_id="evidence-session", worker_id="evidence-worker-new",
        now_epoch=105, lease_seconds=10,
    )
    assert old and new and new["fencing_token"] > old["fencing_token"]

    journal = BackendTradeJournal(backend)
    opportunity = {
        "session_id": "evidence-session", "mode": "simulation", "strategy": "fixture",
        "exchange_a": "alpha", "exchange_b": "beta", "symbol": "BTC/USDT",
        "requested_usd": 10.0, "expected_net_usd": 0.1, "worst_case_net_usd": -0.2,
        "expected_net_bps": 10.0, "worst_case_net_bps": -20.0, "book_age_ms": 5.0,
        "decision": "REJECT", "evidence": {"source": "test"},
    }
    certification = {
        "session_id": "evidence-session", "venue": "alpha", "symbol": "BTC/USDT",
        "notional_usd": 10.0, "live_eligible": False,
        "reasons": ["simulation_only"], "evidence": {"status": "unverified"},
    }
    with pytest.raises(PermissionError):
        journal.append_opportunity_fenced(
            opportunity, owner_user_id="evidence-owner", worker_id=old["worker_id"],
            fencing_token=old["fencing_token"], now_epoch=106,
        )
    with pytest.raises(PermissionError):
        journal.record_certification_fenced(
            **certification, owner_user_id="evidence-owner", worker_id=old["worker_id"],
            fencing_token=old["fencing_token"], now_epoch=106,
        )
    assert journal.append_opportunity_fenced(
        opportunity, owner_user_id="evidence-owner", worker_id=new["worker_id"],
        fencing_token=new["fencing_token"], now_epoch=106,
    ) > 0
    assert journal.record_certification_fenced(
        **certification, owner_user_id="evidence-owner", worker_id=new["worker_id"],
        fencing_token=new["fencing_token"], now_epoch=106,
    ) > 0
    assert len(journal.recent_opportunities()) == 1
    assert journal.latest_certification("alpha")["session_id"] == "evidence-session"
    journal.close()
