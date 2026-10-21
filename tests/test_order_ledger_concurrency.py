import pytest

from arbx.order_reconciliation import OrderLedger


def leg(key, cid):
    return {
        "key": key,
        "venue": "fake",
        "client_order_id": cid,
        "side": "buy",
        "requested": 1.0,
    }


def test_separate_ledger_connections_cannot_admit_overlapping_executions(tmp_path):
    path = tmp_path / "shared.sqlite3"
    first = OrderLedger(path)
    second = OrderLedger(path)
    eid = first.begin("triangular", [leg("leg1", "client-1")])

    with pytest.raises(RuntimeError, match="unresolved prior execution"):
        second.begin("triangular", [leg("leg1", "client-2")])

    assert first.unresolved()[0]["execution_id"] == eid
    assert len(second.db.execute("SELECT * FROM executions").fetchall()) == 1
    first.close()
    second.close()


def test_failed_admission_rolls_back_without_partial_ledger_rows(tmp_path):
    ledger = OrderLedger(tmp_path / "shared.sqlite3")
    with pytest.raises(Exception):
        ledger.begin("triangular", [
            leg("leg1", "duplicate-client"),
            leg("leg2", "duplicate-client"),
        ])
    assert ledger.unresolved() == []
    assert ledger.db.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0
    assert ledger.db.execute("SELECT COUNT(*) FROM order_legs").fetchone()[0] == 0
    ledger.close()
