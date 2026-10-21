import pytest
from arbx.order_reconciliation import OrderLedger


def test_unresolved_execution_survives_reopen_and_blocks_new_intent(tmp_path):
    path = tmp_path / "orders.sqlite3"
    ledger = OrderLedger(path)
    eid = ledger.begin("BTC/USDT", [
        {"key":"buy","venue":"a","client_order_id":"stable-b","side":"buy","requested":1.0},
        {"key":"sell","venue":"b","client_order_id":"stable-s","side":"sell","requested":1.0},
    ])
    ledger.update_leg(eid,"buy",status="UNKNOWN",error="timeout after submit")
    ledger.set_state(eid,"RECONCILIATION_REQUIRED",{"leg":"buy"})
    ledger.close()

    reopened = OrderLedger(path)
    assert reopened.unresolved()[0]["execution_id"] == eid
    with pytest.raises(RuntimeError, match="unresolved prior execution"):
        reopened.begin("ETH/USDT", [
            {"key":"buy","venue":"a","client_order_id":"next-b","side":"buy","requested":1.0},
            {"key":"sell","venue":"b","client_order_id":"next-s","side":"sell","requested":1.0},
        ])
    reopened.close()


def test_completed_execution_does_not_block_next_intent(tmp_path):
    ledger = OrderLedger(tmp_path / "orders.sqlite3")
    eid = ledger.begin("BTC/USDT", [
        {"key":"buy","venue":"a","client_order_id":"stable-b","side":"buy","requested":1.0},
        {"key":"sell","venue":"b","client_order_id":"stable-s","side":"sell","requested":1.0},
    ])
    ledger.set_state(eid,"COMPLETED")
    next_id = ledger.begin("ETH/USDT", [
        {"key":"buy","venue":"a","client_order_id":"next-b","side":"buy","requested":1.0},
        {"key":"sell","venue":"b","client_order_id":"next-s","side":"sell","requested":1.0},
    ])
    assert next_id != eid
    ledger.close()
