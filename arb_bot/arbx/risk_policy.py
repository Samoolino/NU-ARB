"""Pure positive-edge circuit-breaker policy with an optional persistence interface.

The policy denies new trades after loss or uncertainty. Positive modeled PnL is
an input to a pretrade threshold check only; it is never a promise of realized
profit or a permit to submit an order.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Protocol


class PolicyStage(str, Enum):
    ACTIVE = "active"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    AWAITING_OPERATOR_RESTART = "awaiting_operator_restart"


@dataclass(frozen=True, slots=True)
class PolicyState:
    stage: PolicyStage = PolicyStage.ACTIVE
    reason: str | None = None

    @property
    def permits_new_trading(self) -> bool:
        return self.stage is PolicyStage.ACTIVE


@dataclass(frozen=True, slots=True)
class ReconciliationEvidence:
    """Explicit current account facts; None means unknown and fails closed."""

    realized_pnl: float | None
    balances_match: bool | None
    open_orders: bool | None
    partial_fills: bool | None


@dataclass(frozen=True, slots=True)
class IncidentFact:
    """Allow-listed incident facts only; deliberately excludes free-form strings."""

    code: str
    realized_pnl: float | None
    balances_match: bool | None
    open_orders: bool | None
    partial_fills: bool | None
    stage: str

    _CODES = frozenset({
        "realized_loss", "unknown_pnl", "missing_reconciliation",
        "balance_mismatch", "unknown_balance_state", "open_orders_present",
        "unknown_order_state", "partial_fill_present", "unknown_fill_state",
        "persistence_failure", "reconciliation_complete",
    })

    def __post_init__(self) -> None:
        if self.code not in self._CODES:
            raise ValueError("unsupported risk incident code")
        if self.stage not in {stage.value for stage in PolicyStage}:
            raise ValueError("unsupported risk policy stage")
        if self.realized_pnl is not None and (
            isinstance(self.realized_pnl, bool)
            or not isinstance(self.realized_pnl, (int, float))
            or not math.isfinite(self.realized_pnl)
        ):
            raise ValueError("incident PnL must be finite or unknown")
        if any(value is not True and value is not False and value is not None for value in (
            self.balances_match, self.open_orders, self.partial_fills
        )):
            raise ValueError("incident reconciliation facts must be boolean or unknown")


class RiskPolicyStore(Protocol):
    """Minimal durable interface; implementations must atomically persist incidents/state."""

    def load_risk_policy_state(self) -> dict[str, Any] | None: ...
    def save_risk_policy_state(self, state: dict[str, Any]) -> None: ...
    def record_risk_incident(self, incident: IncidentFact,
                             state: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class PretradeDecision:
    allowed: bool
    reason: str


def _finite_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def evaluate_pretrade(expected_pnl: Any, worst_case_pnl: Any,
                      min_expected_pnl: Any, min_worst_case_pnl: Any) -> PretradeDecision:
    """Check positive estimates and positive thresholds; never asserts an outcome."""
    values = (expected_pnl, worst_case_pnl, min_expected_pnl, min_worst_case_pnl)
    if not all(_finite_number(value) for value in values):
        return PretradeDecision(False, "unknown_or_invalid_pnl")
    if min_expected_pnl <= 0 or min_worst_case_pnl <= 0:
        return PretradeDecision(False, "positive_thresholds_required")
    if expected_pnl <= 0 or worst_case_pnl <= 0:
        return PretradeDecision(False, "non_positive_modeled_pnl")
    if expected_pnl < min_expected_pnl or worst_case_pnl < min_worst_case_pnl:
        return PretradeDecision(False, "modeled_pnl_below_threshold")
    return PretradeDecision(True, "thresholds_met_not_guaranteed")


def _state_payload(state: PolicyState) -> dict[str, Any]:
    return {"stage": state.stage.value, "reason": state.reason}


def _restore_state(payload: dict[str, Any]) -> PolicyState:
    if not isinstance(payload, dict):
        raise ValueError("invalid persisted state")
    stage = PolicyStage(payload.get("stage"))
    reason = payload.get("reason")
    if reason is not None and reason not in IncidentFact._CODES:
        raise ValueError("invalid persisted policy reason")
    if ((stage is PolicyStage.ACTIVE and reason is not None)
            or (stage is PolicyStage.AWAITING_OPERATOR_RESTART
                and reason != "reconciliation_complete")
            or (stage is PolicyStage.RECONCILIATION_REQUIRED
                and reason not in IncidentFact._CODES - {"reconciliation_complete"})):
        raise ValueError("inconsistent persisted policy state")
    return PolicyState(stage, reason)


def _event_code(evidence: ReconciliationEvidence | None) -> str:
    if evidence is None:
        return "missing_reconciliation"
    pnl_known = _finite_number(evidence.realized_pnl)
    if not pnl_known:
        return "unknown_pnl"
    if evidence.balances_match is None or not isinstance(evidence.balances_match, bool):
        return "unknown_balance_state"
    if evidence.balances_match is False:
        return "balance_mismatch"
    if evidence.open_orders is None or not isinstance(evidence.open_orders, bool):
        return "unknown_order_state"
    if evidence.open_orders is True:
        return "open_orders_present"
    if evidence.partial_fills is None or not isinstance(evidence.partial_fills, bool):
        return "unknown_fill_state"
    if evidence.partial_fills is True:
        return "partial_fill_present"
    return ""


class PositiveEdgeRiskPolicy:
    """Halt on any realized loss/unknown PnL; require reconciliation and operator restart."""

    def __init__(self, store: RiskPolicyStore | None = None):
        self.store = store
        self._state = PolicyState()
        if store is not None:
            try:
                persisted = store.load_risk_policy_state()
                if persisted is not None:
                    self._state = _restore_state(persisted)
                else:
                    store.save_risk_policy_state(_state_payload(self._state))
            except Exception:
                # A broken or unreadable persisted state is not permission to trade.
                self._state = PolicyState(
                    PolicyStage.RECONCILIATION_REQUIRED, "persistence_failure"
                )

    @property
    def state(self) -> PolicyState:
        return self._state

    @property
    def permits_new_trading(self) -> bool:
        return self.state.permits_new_trading

    @property
    def stage(self) -> PolicyStage:
        return self.state.stage

    def _record_incident(self, code: str, *, realized_pnl: float | None = None,
                         evidence: ReconciliationEvidence | None = None) -> None:
        incident_pnl = realized_pnl
        if incident_pnl is None and evidence is not None and _finite_number(evidence.realized_pnl):
            incident_pnl = float(evidence.realized_pnl)
        incident = IncidentFact(
            code=code,
            realized_pnl=incident_pnl,
            balances_match=None if evidence is None else evidence.balances_match,
            open_orders=None if evidence is None else evidence.open_orders,
            partial_fills=None if evidence is None else evidence.partial_fills,
            stage=self.state.stage.value,
        )
        if self.store is not None:
            try:
                self.store.record_risk_incident(incident, _state_payload(self._state))
            except Exception:
                # Never roll back a halt because incident persistence failed.
                self._state = PolicyState(
                    PolicyStage.RECONCILIATION_REQUIRED, "persistence_failure"
                )
                try:
                    self.store.save_risk_policy_state(_state_payload(self._state))
                except Exception:
                    pass

    def observe_realized_pnl(self, pnl: float | None) -> PretradeDecision:
        """Record a completed outcome; zero/positive do not trip the loss breaker."""
        if not _finite_number(pnl):
            self._state = PolicyState(
                PolicyStage.RECONCILIATION_REQUIRED, "unknown_pnl"
            )
            self._record_incident("unknown_pnl")
            return PretradeDecision(False, "unknown_pnl_reconciliation_required")
        if pnl < 0:
            self._state = PolicyState(
                PolicyStage.RECONCILIATION_REQUIRED, "realized_loss"
            )
            self._record_incident("realized_loss", realized_pnl=float(pnl))
            return PretradeDecision(False, "realized_loss_reconciliation_required")
        return PretradeDecision(
            self.permits_new_trading,
            "thresholds_met_not_guaranteed" if self.permits_new_trading
            else "risk_policy_halted",
        )

    def require_reconciliation(self, code: str = "missing_reconciliation") -> PretradeDecision:
        """Latch an allow-listed execution/account uncertainty until reconciled."""
        if code not in {
            "missing_reconciliation", "unknown_balance_state",
            "balance_mismatch", "unknown_order_state", "open_orders_present",
            "unknown_fill_state", "partial_fill_present", "unknown_pnl",
        }:
            raise ValueError("unsupported reconciliation incident code")
        self._state = PolicyState(PolicyStage.RECONCILIATION_REQUIRED, code)
        self._record_incident(code)
        return PretradeDecision(False, f"{code}_reconciliation_required")

    def reconcile(self, evidence: ReconciliationEvidence | None) -> PretradeDecision:
        """Advance only after explicit known PnL, matching balances, and no open/partial orders."""
        if self.stage is not PolicyStage.RECONCILIATION_REQUIRED:
            return PretradeDecision(False, "reconciliation_not_required_or_restart_pending")
        code = _event_code(evidence)
        if code:
            self._state = PolicyState(
                PolicyStage.RECONCILIATION_REQUIRED, code
            )
            self._record_incident(code, evidence=evidence)
            return PretradeDecision(False, f"{code}_reconciliation_required")
        self._state = PolicyState(
            PolicyStage.AWAITING_OPERATOR_RESTART, "reconciliation_complete"
        )
        if self.store is not None:
            try:
                self.store.save_risk_policy_state(_state_payload(self._state))
            except Exception:
                self._state = PolicyState(
                    PolicyStage.RECONCILIATION_REQUIRED, "persistence_failure"
                )
                try:
                    self.store.save_risk_policy_state(_state_payload(self._state))
                except Exception:
                    pass
                return PretradeDecision(False, "persistence_failure_reconciliation_required")
        return PretradeDecision(False, "explicit_operator_restart_required")

    def restart(self, *, operator_confirmed: bool) -> PretradeDecision:
        """Clear a reconciled halt only after explicit operator confirmation."""
        if (self.stage is not PolicyStage.AWAITING_OPERATOR_RESTART
                or operator_confirmed is not True):
            return PretradeDecision(False, "reconciliation_and_explicit_restart_required")
        prior = self._state
        self._state = PolicyState()
        if self.store is not None:
            try:
                self.store.save_risk_policy_state(_state_payload(self._state))
            except Exception:
                self._state = prior
                try:
                    self.store.save_risk_policy_state(_state_payload(prior))
                except Exception:
                    pass
                return PretradeDecision(False, "persistence_failure_reconciliation_required")
        return PretradeDecision(True, "operator_restart_confirmed")

    def pretrade(self, expected_pnl: Any, worst_case_pnl: Any,
                 min_expected_pnl: Any, min_worst_case_pnl: Any) -> PretradeDecision:
        """Combine circuit-breaker state with modeled threshold inputs only."""
        if not self.permits_new_trading:
            return PretradeDecision(False, "risk_policy_halted")
        return evaluate_pretrade(
            expected_pnl, worst_case_pnl, min_expected_pnl, min_worst_case_pnl
        )


def incident_payload(incident: IncidentFact) -> dict[str, Any]:
    """Canonical sanitized representation for persistence adapters."""
    return asdict(incident)
