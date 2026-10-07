"""Deterministic, route-scoped fail-safe supervision state.

This pure state model records sanitized fault categories and reconciliation
evidence. It has no venue transport, persistence, retry, or execution hooks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .order_lifecycle import OrderLifecycleState


class RouteFault(str, Enum):
    WS_DISCONNECT = "ws_disconnect"
    REST_TIMEOUT = "rest_timeout"
    CLOCK_DRIFT = "clock_drift"
    BALANCE_MISMATCH = "balance_mismatch"
    UNKNOWN_ORDER = "unknown_order"
    PARTIAL_FILL = "partial_fill"
    DUPLICATE_INTENT_ORDER = "duplicate_intent_order"
    STALE_BOOK = "stale_book"
    UNEXPECTED_FEE = "unexpected_fee"
    VENUE_OUTAGE = "venue_outage"
    AUTH_PERMISSION_FAILURE = "auth_permission_failure"
    NONCE_RATE_LIMIT = "nonce_rate_limit"
    NETWORK_MISMATCH = "network_mismatch"


class RouteStatus(str, Enum):
    READY = "ready"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    OPERATOR_RESET_REQUIRED = "operator_reset_required"


class ReconciliationKind(str, Enum):
    ORDER_STATE_RECONCILED = "order_state_reconciled"
    BALANCE_RECONCILED = "balance_reconciled"
    MARKET_DATA_REVALIDATED = "market_data_revalidated"
    VENUE_STATUS_VERIFIED = "venue_status_verified"
    AUTHORIZATION_RESTORED = "authorization_restored"
    NETWORK_ROUTE_VERIFIED = "network_route_verified"
    OPERATOR_REVIEW = "operator_review"


_SAFE_REASON_CODES = {
    fault: f"route_fault_{fault.value}" for fault in RouteFault
}
_SAFE_LIFECYCLE_STATES = frozenset(
    {
        OrderLifecycleState.CREATED,
        OrderLifecycleState.PREFLIGHTED,
        OrderLifecycleState.RESERVED,
        OrderLifecycleState.ACKNOWLEDGED,
        OrderLifecycleState.PARTIALLY_FILLED,
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCEL_PENDING,
        OrderLifecycleState.CANCELED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.SETTLED,
    }
)
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}\Z")


@dataclass(frozen=True, slots=True)
class RouteIncident:
    """An allow-listed route fault; never stores exception or response text."""

    sequence: int
    route_id: str
    fault: RouteFault
    reason_code: str
    lifecycle_state_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationEvidence:
    """Typed evidence recorded after investigation, not an automatic reset."""

    kind: ReconciliationKind
    lifecycle_state_id: str | None = None


@dataclass(frozen=True, slots=True)
class RouteSnapshot:
    route_id: str
    status: RouteStatus
    incidents: tuple[RouteIncident, ...]
    reconciliation_evidence: tuple[ReconciliationEvidence, ...]

    @property
    def blocked(self) -> bool:
        return self.status is not RouteStatus.READY

    @property
    def reconciliation_required(self) -> bool:
        return self.status is RouteStatus.RECONCILIATION_REQUIRED

    @property
    def operator_reset_required(self) -> bool:
        return self.status is RouteStatus.OPERATOR_RESET_REQUIRED

    @property
    def attempt_allowed(self) -> bool:
        return self.status is RouteStatus.READY


@dataclass
class _RouteRecord:
    route_id: str
    status: RouteStatus = RouteStatus.READY
    incidents: list[RouteIncident] = field(default_factory=list)
    evidence: list[ReconciliationEvidence] = field(default_factory=list)

    def snapshot(self) -> RouteSnapshot:
        return RouteSnapshot(
            route_id=self.route_id,
            status=self.status,
            incidents=tuple(self.incidents),
            reconciliation_evidence=tuple(self.evidence),
        )


def _validated_lifecycle_state(value: str | OrderLifecycleState | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, OrderLifecycleState):
        state = value
    elif isinstance(value, str):
        try:
            state = OrderLifecycleState(value)
        except ValueError:
            raise ValueError("lifecycle_state_identifier_unsafe") from None
    else:
        raise ValueError("lifecycle_state_identifier_unsafe")
    if state not in _SAFE_LIFECYCLE_STATES:
        raise ValueError("lifecycle_state_identifier_unsafe")
    return state.value


class RouteSupervisor:
    """In-memory route-state model that stops, but never retries, affected work.

    ``operator_authorized`` is an assertion from the caller, not an
    authentication mechanism. Production authorization and durable recovery
    must be supplied by an integrating system before this model can control
    any execution path.
    """

    def __init__(self) -> None:
        self._routes: dict[str, _RouteRecord] = {}

    def register_route(self, route_id: str) -> RouteSnapshot:
        self._validate_route_id(route_id)
        if route_id in self._routes:
            raise ValueError("route_already_registered")
        route = _RouteRecord(route_id=route_id)
        self._routes[route_id] = route
        return route.snapshot()

    def get(self, route_id: str) -> RouteSnapshot:
        return self._route(route_id).snapshot()

    def assert_attempt_allowed(self, route_id: str) -> None:
        """Fail closed unless the registered route is currently ready."""
        if self._route(route_id).status is not RouteStatus.READY:
            raise PermissionError("route_attempt_blocked")

    def record_fault(
        self,
        route_id: str,
        fault: RouteFault,
        *,
        lifecycle_state_id: str | OrderLifecycleState | None = None,
    ) -> RouteSnapshot:
        route = self._route(route_id)
        if not isinstance(fault, RouteFault):
            raise ValueError("route_fault_unrecognized")
        safe_state = _validated_lifecycle_state(lifecycle_state_id)
        incident = RouteIncident(
            sequence=len(route.incidents) + 1,
            route_id=route_id,
            fault=fault,
            reason_code=_SAFE_REASON_CODES[fault],
            lifecycle_state_id=safe_state,
        )
        route.incidents.append(incident)
        route.evidence.clear()
        route.status = RouteStatus.RECONCILIATION_REQUIRED
        return route.snapshot()

    def record_reconciliation(
        self,
        route_id: str,
        kind: ReconciliationKind,
        *,
        lifecycle_state_id: str | OrderLifecycleState | None = None,
    ) -> RouteSnapshot:
        route = self._route(route_id)
        if not isinstance(kind, ReconciliationKind):
            raise ValueError("reconciliation_kind_unrecognized")
        if route.status is RouteStatus.READY:
            raise ValueError("route_reconciliation_not_required")
        evidence = ReconciliationEvidence(
            kind=kind,
            lifecycle_state_id=_validated_lifecycle_state(lifecycle_state_id),
        )
        if evidence not in route.evidence:
            route.evidence.append(evidence)
        route.status = RouteStatus.OPERATOR_RESET_REQUIRED
        return route.snapshot()

    def operator_reset(
        self,
        route_id: str,
        *,
        operator_id: str,
        operator_authorized: bool,
    ) -> RouteSnapshot:
        route = self._route(route_id)
        if not isinstance(operator_id, str) or not _SAFE_IDENTIFIER.fullmatch(operator_id):
            raise ValueError("operator_id_invalid")
        if operator_authorized is not True:
            raise PermissionError("operator_reset_not_authorized")
        if route.status is RouteStatus.RECONCILIATION_REQUIRED:
            raise ValueError("route_reconciliation_evidence_required")
        if route.status is not RouteStatus.OPERATOR_RESET_REQUIRED:
            raise ValueError("route_operator_reset_not_required")
        route.status = RouteStatus.READY
        return route.snapshot()

    @staticmethod
    def _validate_route_id(route_id: str) -> None:
        if not isinstance(route_id, str) or not _SAFE_IDENTIFIER.fullmatch(route_id):
            raise ValueError("route_id_invalid")

    def _route(self, route_id: str) -> _RouteRecord:
        self._validate_route_id(route_id)
        try:
            return self._routes[route_id]
        except KeyError:
            raise ValueError("route_not_registered") from None
