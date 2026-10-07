from decimal import Decimal as D
import sqlite3

import pytest

from arbx.order_lifecycle import (
    DuplicateOrderError,
    IdempotencyConflict,
    InvalidLifecycleTransition,
    LifecycleAction,
    LifecycleEvent,
    OrderIntent,
    OrderLifecycleState,
    ReconciledOrderState,
    SQLiteOrderRepository,
)


def intent(**overrides):
    values = {
        "intent_id": "intent-1",
        "client_order_id": "client-1",
        "route_id": "route-1",
        "opportunity_id": "opportunity-1",
        "strategy": "cross_exchange",
        "allocation": D("25.00"),
        "quantity": D("0.5"),
        "expected_pnl": D("0.80"),
        "timestamp": "2026-10-07T13:00:00+00:00",
    }
    values.update(overrides)
    return OrderIntent(**values)


def event(event_id, action, **kwargs):
    return LifecycleEvent(
        event_id=event_id,
        action=action,
        timestamp="2026-10-07T13:01:00+00:00",
        **kwargs,
    )


def advance_to_reserved(repository, order_intent):
    record = repository.create_intent(order_intent)
    record = repository.apply(
        record.intent_id, event("preflight", LifecycleAction.PREFLIGHT_PASSED)
    )
    return repository.apply(record.intent_id, event("reserve", LifecycleAction.RESERVED))


def advance_to_submitting(repository, order_intent):
    record = advance_to_reserved(repository, order_intent)
    return repository.apply(
        record.intent_id, event("submit-intent", LifecycleAction.SUBMIT_INTENT)
    )


def test_full_lifecycle_settlement_pnl_and_transport_boundary(tmp_path):
    repository = SQLiteOrderRepository(path=tmp_path / "lifecycle.sqlite3")
    try:
        record = advance_to_submitting(repository, intent())
        assert record.state is OrderLifecycleState.SUBMITTING
        record = repository.apply(
            record.intent_id,
            event("ack", LifecycleAction.ACKNOWLEDGED, venue_order_id="venue-42"),
        )
        record = repository.apply(
            record.intent_id,
            event("fill-1", LifecycleAction.FILL, fill_quantity=D("0.2"),
                  realized_pnl_delta=D("0.1")),
        )
        assert record.state is OrderLifecycleState.PARTIALLY_FILLED
        assert record.filled_quantity == D("0.2")
        assert record.realized_pnl == D("0.1")
        record = repository.apply(
            record.intent_id,
            event("cancel-intent", LifecycleAction.CANCEL_INTENT),
        )
        assert record.state is OrderLifecycleState.CANCEL_PENDING
        record = repository.apply(
            record.intent_id, event("cancelled", LifecycleAction.CANCELED)
        )
        record = repository.apply(
            record.intent_id,
            event("settlement", LifecycleAction.SETTLE, final_realized_pnl=D("-0.03")),
        )

        assert record.state is OrderLifecycleState.SETTLED
        assert record.venue_order_id == "venue-42"
        assert record.realized_pnl == D("-0.03")
        assert record.expected_pnl == D("0.80")
        assert record.allocation == D("25.00")
        assert record.client_order_id == "client-1"
        assert record.route_id == "route-1"
        assert record.opportunity_id == "opportunity-1"
        assert record.strategy == "cross_exchange"
        assert record.timestamp == "2026-10-07T13:00:00+00:00"
        assert not hasattr(repository, "submit")
        assert not hasattr(repository, "cancel")
    finally:
        repository.close()


def test_full_fill_and_preflight_rejection_paths(tmp_path):
    repository = SQLiteOrderRepository(path=tmp_path / "orders.sqlite3")
    try:
        record = advance_to_submitting(repository, intent())
        record = repository.apply(
            record.intent_id,
            event("ack", LifecycleAction.ACKNOWLEDGED, venue_order_id="venue-1"),
        )
        record = repository.apply(
            record.intent_id, event("fill", LifecycleAction.FILL, fill_quantity=D("0.5"))
        )
        assert record.state is OrderLifecycleState.FILLED
        record = repository.apply(
            record.intent_id,
            event("settle", LifecycleAction.SETTLE, final_realized_pnl=D("1.25")),
        )
        assert record.state is OrderLifecycleState.SETTLED
        assert record.realized_pnl == D("1.25")

        rejected = repository.create_intent(intent(intent_id="intent-reject",
                                                  client_order_id="client-reject"))
        rejected = repository.apply(
            rejected.intent_id, event("reject-preflight", LifecycleAction.PREFLIGHT_REJECTED)
        )
        assert rejected.state is OrderLifecycleState.REJECTED
        with pytest.raises(InvalidLifecycleTransition):
            repository.apply(rejected.intent_id, event("submit", LifecycleAction.SUBMIT_INTENT))
    finally:
        repository.close()


def test_duplicate_intent_and_client_ids_are_safe_and_idempotent(tmp_path):
    repository = SQLiteOrderRepository(path=tmp_path / "dupes.sqlite3")
    try:
        first = repository.create_intent(intent())
        assert repository.create_intent(intent()) == first
        progressed = repository.apply(
            first.intent_id, event("preflight", LifecycleAction.PREFLIGHT_PASSED)
        )
        assert repository.create_intent(intent()) == progressed
        with pytest.raises(DuplicateOrderError):
            repository.create_intent(intent(expected_pnl=D("2")))
        with pytest.raises(DuplicateOrderError):
            repository.create_intent(intent(intent_id="intent-2"))
    finally:
        repository.close()


def test_event_idempotency_rejects_payload_reuse_and_overfills_fail_closed(tmp_path):
    repository = SQLiteOrderRepository(path=tmp_path / "events.sqlite3")
    try:
        record = advance_to_submitting(repository, intent())
        ack = event("ack", LifecycleAction.ACKNOWLEDGED, venue_order_id="venue-1")
        assert repository.apply(record.intent_id, ack).revision == 4
        assert repository.apply(record.intent_id, ack).revision == 4
        with pytest.raises(IdempotencyConflict):
            repository.apply(
                record.intent_id,
                event("ack", LifecycleAction.ACKNOWLEDGED, venue_order_id="venue-different"),
            )
        with pytest.raises(InvalidLifecycleTransition):
            repository.apply(
                record.intent_id,
                event("too-much", LifecycleAction.FILL, fill_quantity=D("0.6")),
            )
        assert repository.get(record.intent_id).filled_quantity == 0
    finally:
        repository.close()


@pytest.mark.parametrize("failure_action", [LifecycleAction.LOST_ACK, LifecycleAction.TIMEOUT])
def test_lost_ack_or_submit_timeout_is_unknown_and_cannot_be_resubmitted(
    tmp_path, failure_action,
):
    repository = SQLiteOrderRepository(path=tmp_path / f"{failure_action.value}.sqlite3")
    try:
        record = advance_to_submitting(repository, intent())
        record = repository.apply(record.intent_id, event("uncertain", failure_action))
        assert record.state is OrderLifecycleState.UNKNOWN
        assert record.reconciliation_required
        assert repository.list_reconciliation_required() == (record,)
        with pytest.raises(InvalidLifecycleTransition):
            repository.apply(record.intent_id, event("retry", LifecycleAction.SUBMIT_INTENT))
        with pytest.raises(InvalidLifecycleTransition):
            repository.apply(
                record.intent_id,
                event("ack-after-unknown", LifecycleAction.ACKNOWLEDGED,
                      venue_order_id="venue-1"),
            )
    finally:
        repository.close()


def test_unknown_reconciliation_recovers_ack_partial_and_final_pnl(tmp_path):
    repository = SQLiteOrderRepository(path=tmp_path / "reconcile.sqlite3")
    try:
        record = advance_to_submitting(repository, intent())
        record = repository.apply(record.intent_id, event("lost", LifecycleAction.LOST_ACK))
        record = repository.apply(
            record.intent_id,
            event("lookup-1", LifecycleAction.RECONCILE,
                  reconciled_state=ReconciledOrderState.NOT_FOUND),
        )
        assert record.state is OrderLifecycleState.UNKNOWN
        assert record.reconciliation_required
        record = repository.apply(
            record.intent_id,
            event("lookup-2", LifecycleAction.RECONCILE,
                  reconciled_state=ReconciledOrderState.PARTIALLY_FILLED,
                  venue_order_id="venue-1", cumulative_filled_quantity=D("0.2")),
        )
        assert record.state is OrderLifecycleState.PARTIALLY_FILLED
        assert not record.reconciliation_required
        record = repository.apply(
            record.intent_id,
            event("timeout", LifecycleAction.TIMEOUT),
        )
        assert record.state is OrderLifecycleState.TIMED_OUT
        assert record.reconciliation_required
        record = repository.apply(
            record.intent_id,
            event("lookup-3", LifecycleAction.RECONCILE,
                  reconciled_state=ReconciledOrderState.FILLED,
                  cumulative_filled_quantity=D("0.5")),
        )
        assert record.state is OrderLifecycleState.FILLED
        assert record.filled_quantity == D("0.5")
        record = repository.apply(
            record.intent_id,
            event("settle", LifecycleAction.SETTLE, final_realized_pnl=D("0.04")),
        )
        assert record.state is OrderLifecycleState.SETTLED
        assert record.realized_pnl == D("0.04")
    finally:
        repository.close()


def test_recognized_state_validation_and_unknown_persisted_state_fail_closed(tmp_path):
    connection = sqlite3.connect(":memory:")
    repository = SQLiteOrderRepository(connection=connection)
    try:
        with pytest.raises(ValueError, match="recognized ReconciledOrderState"):
            LifecycleEvent(
                "bad-reconcile", LifecycleAction.RECONCILE, reconciled_state="mystery"
            )
        record = repository.create_intent(intent())
        connection.execute(
            "UPDATE order_lifecycle_orders SET state='surprise' WHERE intent_id=?",
            (record.intent_id,),
        )
        with pytest.raises(RuntimeError, match="unrecognized persisted order state"):
            repository.get(record.intent_id)
    finally:
        repository.close()
        connection.close()


def test_sqlite_recovery_is_durable_and_injected_connection_is_not_closed(tmp_path):
    path = tmp_path / "recover.sqlite3"
    repository = SQLiteOrderRepository(path=path)
    record = advance_to_submitting(repository, intent())
    record = repository.apply(record.intent_id, event("lost", LifecycleAction.LOST_ACK))
    repository.close()

    recovered = SQLiteOrderRepository(path=path)
    try:
        persisted = recovered.get(record.intent_id)
        assert persisted == record
        assert persisted.state is OrderLifecycleState.UNKNOWN
        assert persisted.reconciliation_required
        assert recovered.list_reconciliation_required() == (persisted,)
    finally:
        recovered.close()

    connection = sqlite3.connect(":memory:")
    borrowed = SQLiteOrderRepository(connection=connection)
    borrowed.create_intent(intent())
    borrowed.close()
    assert connection.execute("SELECT COUNT(*) FROM order_lifecycle_orders").fetchone()[0] == 1
    connection.close()


def test_repository_requires_explicit_connection_or_path():
    with pytest.raises(ValueError, match="exactly one"):
        SQLiteOrderRepository()
    with pytest.raises(ValueError, match="exactly one"):
        SQLiteOrderRepository(connection=sqlite3.connect(":memory:"), path="ignored.sqlite3")
