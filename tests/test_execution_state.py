import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.journal import TradeJournal


def test_execution_state_is_durable_and_recoverable(tmp_path):
    path = tmp_path / "journal.sqlite3"
    journal = TradeJournal(path)
    journal.create_execution(
        execution_id="e1", session_id="s1", mode="live",
        strategy="cross_exchange", opportunity_id="o1",
        legs=[
            {"leg_index": 0, "exchange_id": "binance", "symbol": "BTC/USDT", "side": "buy", "requested_amount": 0.001},
            {"leg_index": 1, "exchange_id": "bybit", "symbol": "BTC/USDT", "side": "sell", "requested_amount": 0.001},
        ],
        now=100.0,
    )
    journal.transition_execution("e1", "SUBMITTING", now=101.0)
    journal.transition_leg("e1", 0, "SUBMITTED", order={"id": "b1", "filled": 0.001, "cost": 50.0}, now=101.1)
    journal.transition_leg("e1", 0, "FILLED", order={"id": "b1", "filled": 0.001, "cost": 50.0}, now=101.2)
    journal.close()

    reopened = TradeJournal(path)
    open_runs = reopened.open_executions(mode="live")
    assert open_runs[0]["execution_id"] == "e1"
    assert open_runs[0]["state"] == "SUBMITTING"

    reopened.transition_leg("e1", 1, "SUBMITTING", now=102.0)
    reopened.transition_leg("e1", 1, "LEG_FAILED", error="test failure", now=102.1)
    reopened.transition_execution("e1", "HALTED", error="manual reconciliation required", now=102.2)
    assert reopened.open_executions(mode="live")[0]["state"] == "HALTED"
    reopened.close()


def test_verified_execution_is_not_recovered(tmp_path):
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    journal.create_execution(
        execution_id="e2", session_id="s2", mode="live",
        strategy="cross_exchange", opportunity_id="o2",
        legs=[
            {"leg_index": 0, "exchange_id": "binance", "symbol": "BTC/USDT", "side": "buy", "requested_amount": 0.001},
            {"leg_index": 1, "exchange_id": "bybit", "symbol": "BTC/USDT", "side": "sell", "requested_amount": 0.001},
        ],
        now=200.0,
    )
    journal.transition_execution("e2", "SUBMITTING", now=201.0)
    journal.transition_execution("e2", "SUBMITTED", now=202.0)
    journal.transition_execution("e2", "SETTLEMENT_PENDING", now=203.0)
    journal.transition_execution("e2", "VERIFIED", now=204.0)
    assert journal.open_executions(mode="live") == []
    journal.close()


def test_exchange_authoritative_order_snapshot_is_persisted(tmp_path):
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    journal.create_execution(
        execution_id="e3", session_id="s3", mode="live",
        strategy="cross_exchange", opportunity_id="o3",
        legs=[
            {"leg_index": 0, "exchange_id": "binance", "symbol": "BTC/USDT", "side": "buy", "requested_amount": 0.001},
            {"leg_index": 1, "exchange_id": "bybit", "symbol": "BTC/USDT", "side": "sell", "requested_amount": 0.001},
        ],
    )
    journal.transition_execution("e3", "SUBMITTING")
    journal.transition_leg("e3", 0, "SUBMITTED", order={"id": "b3", "filled": 0.001, "cost": 50.0})
    journal.reconcile_leg("e3", 0, {"id": "b3", "filled": 0.0008, "cost": 40.0})
    row = journal.db.execute(
        "SELECT order_id, filled, cost FROM execution_legs WHERE execution_id='e3' AND leg_index=0"
    ).fetchone()
    assert row["order_id"] == "b3"
    assert row["filled"] == 0.0008
    assert row["cost"] == 40.0
    journal.close()
