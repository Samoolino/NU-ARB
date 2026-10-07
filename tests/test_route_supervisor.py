import pytest

from arbx.order_lifecycle import OrderLifecycleState
from arbx.route_supervisor import (
    ReconciliationKind,
    RouteFault,
    RouteStatus,
    RouteSupervisor,
)


@pytest.mark.parametrize(
    ("fault", "reason_code"),
    [
        (RouteFault.WS_DISCONNECT, "route_fault_ws_disconnect"),
        (RouteFault.REST_TIMEOUT, "route_fault_rest_timeout"),
        (RouteFault.CLOCK_DRIFT, "route_fault_clock_drift"),
        (RouteFault.BALANCE_MISMATCH, "route_fault_balance_mismatch"),
        (RouteFault.UNKNOWN_ORDER, "route_fault_unknown_order"),
        (RouteFault.PARTIAL_FILL, "route_fault_partial_fill"),
        (RouteFault.DUPLICATE_INTENT_ORDER, "route_fault_duplicate_intent_order"),
        (RouteFault.STALE_BOOK, "route_fault_stale_book"),
        (RouteFault.UNEXPECTED_FEE, "route_fault_unexpected_fee"),
        (RouteFault.VENUE_OUTAGE, "route_fault_venue_outage"),
        (RouteFault.AUTH_PERMISSION_FAILURE, "route_fault_auth_permission_failure"),
        (RouteFault.NONCE_RATE_LIMIT, "route_fault_nonce_rate_limit"),
        (RouteFault.NETWORK_MISMATCH, "route_fault_network_mismatch"),
    ],
)
def test_each_enumerated_fault_blocks_only_its_route(fault, reason_code):
    supervisor = RouteSupervisor()
    supervisor.register_route("route-a")
    supervisor.register_route("route-b")

    stopped = supervisor.record_fault("route-a", fault)

    assert stopped.status is RouteStatus.RECONCILIATION_REQUIRED
    assert stopped.blocked and stopped.reconciliation_required
    assert not stopped.attempt_allowed
    with pytest.raises(PermissionError, match="route_attempt_blocked"):
        supervisor.assert_attempt_allowed("route-a")
    assert supervisor.assert_attempt_allowed("route-b") is None
    assert stopped.incidents[0].reason_code == reason_code
    assert stopped.incidents[0].fault is fault
    assert supervisor.get("route-b").attempt_allowed
    assert supervisor.get("route-b").incidents == ()


def test_fault_has_no_exception_or_venue_payload_and_reason_is_allow_listed():
    supervisor = RouteSupervisor()
    supervisor.register_route("route-1")
    incident = supervisor.record_fault("route-1", RouteFault.VENUE_OUTAGE).incidents[0]

    assert set(incident.__dataclass_fields__) == {
        "sequence", "route_id", "fault", "reason_code", "lifecycle_state_id"
    }
    assert incident.reason_code == "route_fault_venue_outage"


def test_reconciliation_does_not_resume_until_explicit_authorized_reset():
    supervisor = RouteSupervisor()
    supervisor.register_route("route-1")
    supervisor.record_fault("route-1", RouteFault.UNKNOWN_ORDER)

    with pytest.raises(ValueError, match="route_reconciliation_evidence_required"):
        supervisor.operator_reset(
            "route-1", operator_id="operator-1", operator_authorized=True
        )
    with pytest.raises(PermissionError, match="operator_reset_not_authorized"):
        supervisor.operator_reset(
            "route-1", operator_id="operator-1", operator_authorized=False
        )

    reconciled = supervisor.record_reconciliation(
        "route-1",
        ReconciliationKind.ORDER_STATE_RECONCILED,
        lifecycle_state_id=OrderLifecycleState.CANCELED,
    )
    assert reconciled.status is RouteStatus.OPERATOR_RESET_REQUIRED
    assert reconciled.blocked and reconciled.operator_reset_required
    assert not reconciled.reconciliation_required
    assert not reconciled.attempt_allowed

    reset = supervisor.operator_reset(
        "route-1", operator_id="operator-1", operator_authorized=True
    )
    assert reset.status is RouteStatus.READY
    assert reset.attempt_allowed
    assert supervisor.assert_attempt_allowed("route-1") is None
    assert len(reset.incidents) == 1
    assert len(reset.reconciliation_evidence) == 1


def test_new_fault_invalidates_old_evidence_and_duplicate_evidence_is_idempotent():
    supervisor = RouteSupervisor()
    supervisor.register_route("route-1")
    supervisor.record_fault("route-1", RouteFault.STALE_BOOK)
    evidence = supervisor.record_reconciliation(
        "route-1", ReconciliationKind.MARKET_DATA_REVALIDATED
    )
    assert supervisor.record_reconciliation(
        "route-1", ReconciliationKind.MARKET_DATA_REVALIDATED
    ) == evidence

    faulted_again = supervisor.record_fault("route-1", RouteFault.WS_DISCONNECT)
    assert faulted_again.status is RouteStatus.RECONCILIATION_REQUIRED
    assert faulted_again.reconciliation_evidence == ()
    assert [incident.sequence for incident in faulted_again.incidents] == [1, 2]


@pytest.mark.parametrize(
    "unsafe_state",
    [
        OrderLifecycleState.SUBMITTING,
        OrderLifecycleState.TIMED_OUT,
        OrderLifecycleState.UNKNOWN,
        "not_a_lifecycle_state",
    ],
)
def test_phase16_ambiguous_or_unrecognized_state_identifiers_fail_closed(unsafe_state):
    supervisor = RouteSupervisor()
    supervisor.register_route("route-1")

    with pytest.raises(ValueError, match="lifecycle_state_identifier_unsafe"):
        supervisor.record_fault(
            "route-1", RouteFault.UNKNOWN_ORDER, lifecycle_state_id=unsafe_state
        )
    assert supervisor.get("route-1").attempt_allowed


@pytest.mark.parametrize(
    "safe_state",
    [
        OrderLifecycleState.RESERVED,
        OrderLifecycleState.ACKNOWLEDGED,
        OrderLifecycleState.PARTIALLY_FILLED,
        OrderLifecycleState.SETTLED,
        "canceled",
    ],
)
def test_phase16_safe_state_identifiers_are_accepted(safe_state):
    supervisor = RouteSupervisor()
    supervisor.register_route("route-1")

    incident = supervisor.record_fault(
        "route-1", RouteFault.PARTIAL_FILL, lifecycle_state_id=safe_state
    ).incidents[0]
    assert incident.lifecycle_state_id == (
        safe_state.value if isinstance(safe_state, OrderLifecycleState) else safe_state
    )


def test_unrecognized_fault_evidence_and_route_are_rejected():
    supervisor = RouteSupervisor()
    supervisor.register_route("route-1")
    with pytest.raises(ValueError, match="route_fault_unrecognized"):
        supervisor.record_fault("route-1", "ws_disconnect")
    with pytest.raises(ValueError, match="route_not_registered"):
        supervisor.record_fault("route-unknown", RouteFault.WS_DISCONNECT)

    supervisor.record_fault("route-1", RouteFault.WS_DISCONNECT)
    with pytest.raises(ValueError, match="reconciliation_kind_unrecognized"):
        supervisor.record_reconciliation("route-1", "verified")


def test_operator_reset_requires_safe_operator_identifier():
    supervisor = RouteSupervisor()
    supervisor.register_route("route-1")
    supervisor.record_fault("route-1", RouteFault.VENUE_OUTAGE)
    supervisor.record_reconciliation("route-1", ReconciliationKind.VENUE_STATUS_VERIFIED)

    with pytest.raises(ValueError, match="operator_id_invalid"):
        supervisor.operator_reset(
            "route-1", operator_id="operator\nsecret", operator_authorized=True
        )
    assert not supervisor.get("route-1").attempt_allowed


def test_model_does_not_expose_retry_or_transport_operations():
    supervisor = RouteSupervisor()
    assert not hasattr(supervisor, "retry")
    assert not hasattr(supervisor, "submit")
    assert not hasattr(supervisor, "connect")
