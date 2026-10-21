import pytest

from arbx.order_reconciliation import OrderLedger


def _begin(ledger):
    return ledger.begin("triangular", [{
        "key": "leg1",
        "venue": "fake",
        "client_order_id": "nuarb-test-leg1",
        "side": "buy",
        "requested": 1.0,
    }])


def test_unresolved_execution_survives_ledger_restart(tmp_path):
    path = tmp_path / "orders.sqlite3"
    first = OrderLedger(path)
    eid = _begin(first)
    first.set_state(eid, "RECONCILIATION_REQUIRED", {"reason": "partial fill"})
    first.close()

    second = OrderLedger(path)
    assert second.unresolved()[0]["execution_id"] == eid
    snapshot = second.get_execution(eid)
    assert snapshot["state"] == "RECONCILIATION_REQUIRED"
    assert snapshot["detail"]["reason"] == "partial fill"
    assert snapshot["legs"][0]["client_order_id"] == "nuarb-test-leg1"
    with pytest.raises(RuntimeError, match="unresolved prior execution"):
        _begin(second)
    second.close()


def test_manual_resolution_requires_explicit_ack_and_evidence(tmp_path):
    ledger = OrderLedger(tmp_path / "orders.sqlite3")
    eid = _begin(ledger)
    ledger.set_state(eid, "RECONCILIATION_REQUIRED", {"reason": "fee evidence missing"})

    with pytest.raises(ValueError, match="confirm=True"):
        ledger.resolve_manual(eid, operator="operator", evidence="checked balances")
    with pytest.raises(ValueError, match="operator and reconciliation evidence"):
        ledger.resolve_manual(eid, operator="", evidence="", confirm=True)

    ledger.resolve_manual(
        eid,
        operator="ops@example.invalid",
        evidence="Exchange order/trade history and free balances reconciled; residual inventory recorded in incident ticket INC-123.",
        confirm=True,
    )
    assert ledger.unresolved() == []
    snapshot = ledger.get_execution(eid)
    assert snapshot["state"] == "RESOLVED_MANUALLY"
    assert snapshot["detail"]["manual_resolution"]["acknowledgement_only"] is True
    assert snapshot["detail"]["manual_resolution"]["previous_state"] == "RECONCILIATION_REQUIRED"
    ledger.close()


def test_cannot_manually_resolve_completed_or_unknown_execution_id(tmp_path):
    ledger = OrderLedger(tmp_path / "orders.sqlite3")
    eid = _begin(ledger)
    ledger.set_state(eid, "COMPLETED", {"pnl": 0.1})
    with pytest.raises(ValueError, match="not unresolved"):
        ledger.resolve_manual(eid, operator="operator", evidence="evidence", confirm=True)
    with pytest.raises(KeyError, match="unknown execution"):
        ledger.resolve_manual("does-not-exist", operator="operator", evidence="evidence", confirm=True)
    ledger.close()
