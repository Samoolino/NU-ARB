"""Fail-closed authorization at live order-submission boundaries."""
from __future__ import annotations

import hashlib
import math
import os
import time
from collections import deque
from dataclasses import dataclass


class ExecutionAuthorizationError(RuntimeError):
    """A live order was denied before it reached an exchange adapter."""


@dataclass(frozen=True, slots=True)
class OrderScope:
    """One exact venue order a permit may submit (quantity is an upper bound)."""

    index: int
    venue: str
    symbol: str
    side: str
    order_type: str
    max_quantity: float
    price: float | None


@dataclass(frozen=True, slots=True)
class ExecutionOrder:
    """Normalized order identity checked immediately before adapter submission."""

    index: int
    venue: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float | None


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    """Immutable live evidence snapshot; unknown fields deliberately default false."""

    authenticated_credentials: bool = False
    venue_live_eligible: bool = False
    execution_eligible: bool = False
    active_market: bool = False
    fresh_orderbook: bool = False
    sufficient_depth: bool = False
    sufficient_balance: bool = False
    sufficient_capital: bool = False
    known_fees: bool = False
    latency_acceptable: bool = False
    risk_approved: bool = False
    rate_limit_ok: bool = False
    venue_healthy: bool = False
    route_certified: bool = False
    expected_pnl: float | None = None
    min_expected_pnl: float | None = None
    worst_case_pnl: float | None = None
    min_worst_case_pnl: float | None = None
    expected_bps: float | None = None
    min_expected_bps: float | None = None
    worst_case_bps: float | None = None
    min_worst_case_bps: float | None = None
    orderbook_observed_at: float | None = None
    max_book_age_ms: float | None = None


@dataclass(frozen=True, slots=True)
class ExecutionPermit:
    """Immutable, single-attempt authority scoped to one opportunity and route."""

    _gate_token: object
    permit_id: str
    strategy: str
    venues: tuple[str, ...]
    route_id: str
    opportunity_id: str
    evidence: ExecutionEvidence
    orders: tuple[OrderScope, ...]
    issued_at: float


@dataclass(frozen=True, slots=True)
class EmergencyExecutionPermit:
    """One same-attempt, one-order recovery authorization."""

    _gate_token: object
    permit_id: str
    parent_permit_id: str
    order: ExecutionOrder


def opportunity_fingerprint(strategy: str, opportunity) -> str:
    """Stable identity for the modeled opportunity being authorized/executed."""
    if strategy == "triangular":
        values = (
            getattr(opportunity, "name", None),
            getattr(opportunity, "start", None),
            getattr(opportunity, "start_asset", None),
            getattr(opportunity, "expected_final", None),
            getattr(opportunity, "worst_final", None),
            getattr(opportunity, "net_bps", None),
            getattr(opportunity, "worst_bps", None),
            tuple((getattr(leg, "symbol", None), getattr(leg, "side", None))
                  for leg in getattr(opportunity, "legs", ())),
            tuple(getattr(opportunity, "path", ())),
            tuple(getattr(opportunity, "limits", ())),
        )
    elif strategy == "cross":
        values = tuple(getattr(opportunity, name, None) for name in (
            "symbol", "buy_ex", "sell_ex", "base", "limit_buy", "limit_sell",
            "cost", "expected_usd", "worst_usd", "net_bps", "worst_bps",
        ))
    else:
        raise ExecutionAuthorizationError("unknown_execution_strategy")
    return hashlib.sha256(repr((strategy, values)).encode("utf-8")).hexdigest()


class ExecutionGate:
    """Authorize only explicitly evidenced orders and re-check switches per submit."""

    _REQUIRED_EVIDENCE = (
        "authenticated_credentials", "venue_live_eligible", "execution_eligible",
        "active_market", "fresh_orderbook", "sufficient_depth", "sufficient_balance",
        "sufficient_capital", "known_fees", "latency_acceptable", "risk_approved",
        "rate_limit_ok", "venue_healthy", "route_certified",
    )

    def __init__(self, cfg, risk_manager=None):
        self.cfg = cfg
        self.risk_manager = risk_manager
        self._token = object()
        self._used_orders: dict[str, set[int]] = {}
        self._authorized_orders: dict[str, dict[int, ExecutionOrder]] = {}
        self._used_recovery: set[str] = set()
        self._attempted: set[str] = set()
        self._permit_stamps: deque[float] = deque()

    def _deny_reason(self, strategy: str) -> str | None:
        if getattr(self.cfg, "mode", "paper") != "live":
            return "paper_mode"
        if os.getenv("BOT_MODE", "") != "live":
            return "BOT_MODE_not_live"
        if os.getenv("BOT_ALLOW_ORDERS", "0") != "1":
            return "BOT_ALLOW_ORDERS_not_enabled"
        if os.getenv("ARBX_LIVE_TRADING_ENABLED", "0") != "1":
            return "ARBX_LIVE_TRADING_ENABLED_not_enabled"

        disabled_aliases = sorted(
            name for name, value in os.environ.items()
            if name.startswith("BOT_ALLOW_") and value.strip() != "1"
        )
        if disabled_aliases:
            return f"{disabled_aliases[0]}_disabled"

        if strategy == "cross":
            if getattr(self.cfg, "cross_live", False) is not True:
                return "cross_live_not_selected"
            if os.getenv("BOT_CROSS_LIVE", "0") != "1":
                return "BOT_CROSS_LIVE_not_enabled"
        elif strategy == "triangular":
            if getattr(self.cfg, "triangular_live", False) is not True:
                return "triangular_live_not_selected"
            if os.getenv("BOT_TRIANGULAR_LIVE", "0") != "1":
                return "BOT_TRIANGULAR_LIVE_not_enabled"
        else:
            return "unsupported_execution_strategy"
        return None

    def check_environment(self, strategy: str = "cross") -> None:
        """Check operator switches before venue startup; submission checks repeat them."""
        reason = self._deny_reason(strategy)
        if reason:
            raise ExecutionAuthorizationError(reason)

    def _check_evidence(self, evidence: ExecutionEvidence, issued_at: float) -> None:
        if not isinstance(evidence, ExecutionEvidence):
            raise ExecutionAuthorizationError("typed_execution_evidence_required")
        for name in self._REQUIRED_EVIDENCE:
            if getattr(evidence, name) is not True:
                raise ExecutionAuthorizationError(f"{name}_not_verified")

        numeric = (
            ("expected_pnl", "min_expected_pnl"),
            ("worst_case_pnl", "min_worst_case_pnl"),
            ("expected_bps", "min_expected_bps"),
            ("worst_case_bps", "min_worst_case_bps"),
        )
        for pnl_name, threshold_name in numeric:
            pnl, threshold = getattr(evidence, pnl_name), getattr(evidence, threshold_name)
            if (not isinstance(pnl, (int, float)) or isinstance(pnl, bool)
                    or not isinstance(threshold, (int, float)) or isinstance(threshold, bool)
                    or not math.isfinite(pnl) or not math.isfinite(threshold)
                    or pnl <= 0 or threshold < 0 or pnl < threshold):
                raise ExecutionAuthorizationError(f"{pnl_name}_below_threshold_or_unknown")

        observed_at, max_age = evidence.orderbook_observed_at, evidence.max_book_age_ms
        if (not isinstance(observed_at, (int, float)) or isinstance(observed_at, bool)
                or not isinstance(max_age, (int, float)) or isinstance(max_age, bool)
                or not math.isfinite(observed_at) or not math.isfinite(max_age)
                or max_age <= 0):
            raise ExecutionAuthorizationError("orderbook_freshness_unknown")
        age_ms = (time.monotonic() - observed_at) * 1000.0
        if age_ms < 0 or age_ms > max_age:
            raise ExecutionAuthorizationError("orderbook_stale_at_submission")

        # The complete evidence snapshot, including venue health and balances, is
        # bounded to the same short interval as its book evidence.
        if (time.monotonic() - issued_at) * 1000.0 > max_age:
            raise ExecutionAuthorizationError("execution_evidence_stale_at_submission")

    def _reserve_rate_limit(self, now: float) -> None:
        while self._permit_stamps and now - self._permit_stamps[0] > 60.0:
            self._permit_stamps.popleft()
        maximum = getattr(self.cfg, "max_trades_per_min", 30)
        if (not isinstance(maximum, int) or isinstance(maximum, bool)
                or maximum <= 0 or len(self._permit_stamps) >= maximum):
            raise ExecutionAuthorizationError("rate_limit_exceeded")
        if self.risk_manager is not None and (
            self.risk_manager.halted or not self.risk_manager.rate_ok()
        ):
            raise ExecutionAuthorizationError("risk_or_rate_limit_no_longer_approved")
        self._permit_stamps.append(now)

    def _check_current_risk(self) -> None:
        if self.risk_manager is not None and self.risk_manager.halted:
            raise ExecutionAuthorizationError("risk_halted_at_submission")
        if self.risk_manager is not None and not self.risk_manager.rate_ok():
            raise ExecutionAuthorizationError("rate_limit_exceeded")

    def authorize(self, strategy: str, *, venue_ids: tuple[str, ...] | list[str] = (),
                  route_id: str = "", opportunity_id: str = "",
                  evidence: ExecutionEvidence | None = None,
                  orders: tuple[OrderScope, ...] | list[OrderScope] = ()) -> ExecutionPermit:
        """Mint a typed permit only when all required evidence and scope are present."""
        self.check_environment(strategy)
        if (not venue_ids or not isinstance(route_id, str) or not route_id
                or not isinstance(opportunity_id, str) or not opportunity_id or not orders):
            raise ExecutionAuthorizationError("execution_scope_incomplete")
        immutable_orders = tuple(orders)
        if (len({order.index for order in immutable_orders}) != len(immutable_orders)
                or any(not isinstance(order, OrderScope) for order in immutable_orders)):
            raise ExecutionAuthorizationError("invalid_order_scope")
        for order in immutable_orders:
            if (order.venue not in venue_ids or not order.symbol or order.side not in ("buy", "sell")
                    or order.order_type != "limit" or order.max_quantity <= 0
                    or not math.isfinite(order.max_quantity)
                    or (order.price is not None
                        and (not math.isfinite(order.price) or order.price <= 0))):
                raise ExecutionAuthorizationError("invalid_order_scope")
        now = time.monotonic()
        self._check_evidence(evidence, now)
        self._reserve_rate_limit(now)
        permit_id = hashlib.sha256(
            f"{id(self)}:{now:.9f}:{strategy}:{route_id}:{opportunity_id}".encode()
        ).hexdigest()
        permit = ExecutionPermit(
            self._token, permit_id, strategy, tuple(venue_ids), route_id,
            opportunity_id, evidence, immutable_orders, now,
        )
        self._used_orders[permit_id] = set()
        self._authorized_orders[permit_id] = {}
        return permit

    def authorize_order(self, permit: ExecutionPermit, order: ExecutionOrder,
                        *, strategy: str, route_id: str, opportunity_id: str) -> None:
        """Recheck current kill switches and exact permit scope before each venue call."""
        self._validate_permit(permit)
        if permit.strategy != strategy:
            raise ExecutionAuthorizationError("execution_permit_scope_mismatch")
        reason = self._deny_reason(strategy)
        if reason:
            raise ExecutionAuthorizationError(reason)
        self._check_current_risk()
        if (permit.strategy != strategy or permit.route_id != route_id
                or permit.opportunity_id != opportunity_id):
            raise ExecutionAuthorizationError("execution_permit_scope_mismatch")
        if not isinstance(order, ExecutionOrder):
            raise ExecutionAuthorizationError("typed_order_scope_required")
        scopes = [scope for scope in permit.orders if scope.index == order.index]
        if len(scopes) != 1:
            raise ExecutionAuthorizationError("order_not_in_permit")
        scope = scopes[0]
        if (scope.venue != order.venue or scope.symbol != order.symbol
                or scope.side != order.side or scope.order_type != order.order_type
                or order.quantity <= 0 or order.quantity > scope.max_quantity
                or scope.price != order.price):
            raise ExecutionAuthorizationError("order_not_in_permit")
        if not math.isfinite(order.quantity):
            raise ExecutionAuthorizationError("order_not_in_permit")
        if order.index in self._used_orders[permit.permit_id]:
            raise ExecutionAuthorizationError("execution_permit_order_reused")
        self._check_evidence(permit.evidence, permit.issued_at)
        self._authorized_orders.setdefault(permit.permit_id, {})[order.index] = order

    def note_order_attempt(self, permit: ExecutionPermit, order: ExecutionOrder) -> None:
        """Consume one scoped order immediately before the exchange adapter call."""
        self._validate_permit(permit)
        authorized = self._authorized_orders.get(permit.permit_id, {}).pop(order.index, None)
        if authorized != order or order.index in self._used_orders.get(permit.permit_id, set()):
            raise ExecutionAuthorizationError("execution_permit_order_reused")
        self._used_orders[permit.permit_id].add(order.index)
        self._attempted.add(permit.permit_id)

    def authorize_recovery(self, permit: ExecutionPermit,
                           order: ExecutionOrder) -> EmergencyExecutionPermit:
        """Mint one emergency permit for an opposite-side order in the same attempt."""
        self._validate_permit(permit)
        reason = self._deny_reason(permit.strategy)
        if reason:
            raise ExecutionAuthorizationError(reason)
        self._check_current_risk()
        if permit.strategy != "triangular" or permit.permit_id not in self._attempted:
            raise ExecutionAuthorizationError("emergency_recovery_not_authorized")
        originals = [scope for scope in permit.orders
                     if scope.venue == order.venue and scope.symbol == order.symbol
                     and scope.side != order.side and scope.order_type == "limit"]
        if (len(originals) != 1 or order.order_type != "market"
                or not math.isfinite(order.quantity) or order.quantity <= 0
                or order.quantity > originals[0].max_quantity):
            raise ExecutionAuthorizationError("emergency_recovery_scope_mismatch")
        self._check_evidence(permit.evidence, permit.issued_at)
        recovery_id = hashlib.sha256(
            f"{permit.permit_id}:recovery:{order.index}:{time.monotonic():.9f}".encode()
        ).hexdigest()
        self._used_recovery.add(recovery_id)
        return EmergencyExecutionPermit(self._token, recovery_id, permit.permit_id, order)

    def authorize_recovery_order(self, permit: ExecutionPermit,
                                 emergency: EmergencyExecutionPermit) -> None:
        """Recheck the global kill switch and exact recovery scope at submit time."""
        self._validate_permit(permit)
        reason = self._deny_reason("triangular")
        if reason:
            raise ExecutionAuthorizationError(reason)
        self._check_current_risk()
        if (not isinstance(emergency, EmergencyExecutionPermit)
                or emergency._gate_token is not self._token
                or emergency.parent_permit_id != permit.permit_id
                or emergency.permit_id not in self._used_recovery
                or permit.permit_id not in self._attempted):
            raise ExecutionAuthorizationError("invalid_emergency_recovery_permit")
        self._check_evidence(permit.evidence, permit.issued_at)
        self._used_recovery.remove(emergency.permit_id)

    def _validate_permit(self, permit: ExecutionPermit) -> None:
        if (not isinstance(permit, ExecutionPermit)
                or permit._gate_token is not self._token
                or permit.permit_id not in self._used_orders):
            raise ExecutionAuthorizationError("invalid_execution_permit")
