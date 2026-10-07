"""Typed, durable order lifecycle intent state; deliberately no venue transport."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from os import PathLike
from pathlib import Path
from typing import Any


class OrderLifecycleState(str, Enum):
    CREATED = "created"
    PREFLIGHTED = "preflighted"
    RESERVED = "reserved"
    SUBMITTING = "submitting"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"
    REJECTED = "rejected"
    SETTLED = "settled"


class LifecycleAction(str, Enum):
    PREFLIGHT_PASSED = "preflight_passed"
    PREFLIGHT_REJECTED = "preflight_rejected"
    RESERVED = "reserved"
    ABORT = "abort"
    SUBMIT_INTENT = "submit_intent"
    ACKNOWLEDGED = "acknowledged"
    FILL = "fill"
    CANCEL_INTENT = "cancel_intent"
    CANCELED = "canceled"
    TIMEOUT = "timeout"
    LOST_ACK = "lost_ack"
    RECONCILE = "reconcile"
    SETTLE = "settle"


class ReconciledOrderState(str, Enum):
    NOT_FOUND = "not_found"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


class DuplicateOrderError(ValueError):
    """An intent or client order ID was reused with conflicting order data."""


class IdempotencyConflict(ValueError):
    """An event ID was reused with different event data."""


class InvalidLifecycleTransition(ValueError):
    """A lifecycle event is invalid for the persisted order state."""


def _decimal(value: object, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        raise ValueError(f"{field} must be a finite decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{field} must be a finite decimal") from None
    if not result.is_finite() or (positive and result <= 0):
        qualifier = "positive and finite" if positive else "finite"
        raise ValueError(f"{field} must be {qualifier}")
    return result


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _timestamp(value: str) -> str:
    _required_text(value, "timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("timestamp must be an ISO-8601 datetime") from None
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """Immutable order intent captured before any external transport action."""

    intent_id: str
    client_order_id: str
    route_id: str
    opportunity_id: str
    strategy: str
    allocation: Decimal
    quantity: Decimal
    expected_pnl: Decimal
    timestamp: str = ""

    def __post_init__(self) -> None:
        for field in (
            "intent_id", "client_order_id", "route_id", "opportunity_id", "strategy",
        ):
            _required_text(getattr(self, field), field)
        object.__setattr__(self, "allocation", _decimal(self.allocation, "allocation", positive=True))
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", positive=True))
        object.__setattr__(self, "expected_pnl", _decimal(self.expected_pnl, "expected_pnl"))
        if not self.timestamp:
            object.__setattr__(self, "timestamp", _now())
        else:
            _timestamp(self.timestamp)


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """A typed state fact, not a request to perform an exchange operation."""

    event_id: str
    action: LifecycleAction
    timestamp: str = ""
    venue_order_id: str | None = None
    fill_quantity: Decimal | None = None
    realized_pnl_delta: Decimal | None = None
    final_realized_pnl: Decimal | None = None
    reconciled_state: ReconciledOrderState | None = None
    cumulative_filled_quantity: Decimal | None = None

    def __post_init__(self) -> None:
        _required_text(self.event_id, "event_id")
        if not isinstance(self.action, LifecycleAction):
            raise ValueError("action must be a recognized LifecycleAction")
        if not self.timestamp:
            object.__setattr__(self, "timestamp", _now())
        else:
            _timestamp(self.timestamp)
        if self.venue_order_id is not None:
            _required_text(self.venue_order_id, "venue_order_id")
        for field in ("fill_quantity", "cumulative_filled_quantity"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _decimal(value, field, positive=True))
        for field in ("realized_pnl_delta", "final_realized_pnl"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _decimal(value, field))
        if self.reconciled_state is not None and not isinstance(
            self.reconciled_state, ReconciledOrderState
        ):
            raise ValueError("reconciled_state must be a recognized ReconciledOrderState")


@dataclass(frozen=True, slots=True)
class OrderRecord:
    intent_id: str
    client_order_id: str
    venue_order_id: str | None
    route_id: str
    opportunity_id: str
    timestamp: str
    strategy: str
    allocation: Decimal
    expected_pnl: Decimal
    realized_pnl: Decimal
    quantity: Decimal
    filled_quantity: Decimal
    state: OrderLifecycleState
    reconciliation_required: bool
    updated_at: str
    revision: int


class SQLiteOrderRepository:
    """SQLite storage over an explicitly injected connection or database path.

    Exactly one of ``connection`` or ``path`` must be supplied. This class
    never discovers or opens the production trade journal implicitly.
    """

    def __init__(
        self,
        *,
        connection: sqlite3.Connection | None = None,
        path: str | PathLike[str] | None = None,
    ) -> None:
        if (connection is None) == (path is None):
            raise ValueError("provide exactly one injected SQLite connection or path")
        if connection is not None and not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be a sqlite3.Connection")
        if path is not None:
            db_path = Path(path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(db_path, timeout=10.0)
            self._owns_connection = True
        else:
            self._owns_connection = False
        assert connection is not None
        self.connection = connection
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS order_lifecycle_orders (
                intent_id TEXT PRIMARY KEY,
                client_order_id TEXT NOT NULL UNIQUE,
                venue_order_id TEXT,
                route_id TEXT NOT NULL,
                opportunity_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                strategy TEXT NOT NULL,
                allocation TEXT NOT NULL,
                expected_pnl TEXT NOT NULL,
                realized_pnl TEXT NOT NULL,
                quantity TEXT NOT NULL,
                filled_quantity TEXT NOT NULL,
                state TEXT NOT NULL,
                reconciliation_required INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS order_lifecycle_events (
                intent_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                resulting_revision INTEGER NOT NULL,
                PRIMARY KEY (intent_id, event_id),
                FOREIGN KEY (intent_id) REFERENCES order_lifecycle_orders(intent_id)
            )"""
        )

    def _transaction(self):
        """Provide an atomic savepoint, including for caller-owned transactions."""
        return _Savepoint(self.connection)

    def create_intent(self, intent: OrderIntent) -> OrderRecord:
        if not isinstance(intent, OrderIntent):
            raise TypeError("typed OrderIntent required")
        values = (
            intent.intent_id, intent.client_order_id, intent.route_id,
            intent.opportunity_id, intent.timestamp, intent.strategy,
            str(intent.allocation), str(intent.expected_pnl), str(Decimal(0)),
            str(intent.quantity), str(Decimal(0)), OrderLifecycleState.CREATED.value,
            0, intent.timestamp, 0,
        )
        with self._transaction():
            row = self._fetch_one(
                "SELECT * FROM order_lifecycle_orders WHERE intent_id=?", (intent.intent_id,)
            )
            if row is not None:
                existing = self._record(row)
                if not self._matches_intent(existing, intent):
                    raise DuplicateOrderError("intent_id already exists with different order data")
                return existing
            try:
                self.connection.execute(
                    """INSERT INTO order_lifecycle_orders (
                        intent_id, client_order_id, route_id, opportunity_id, timestamp,
                        strategy, allocation, expected_pnl, realized_pnl, quantity,
                        filled_quantity, state, reconciliation_required, updated_at, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (values[0], values[1], values[2], values[3], values[4], values[5],
                     values[6], values[7], values[8], values[9], values[10], values[11],
                     values[12], values[13], values[14]),
                )
            except sqlite3.IntegrityError as error:
                raise DuplicateOrderError(
                    "client_order_id already belongs to a different intent"
                ) from error
            return self.get(intent.intent_id)

    def get(self, intent_id: str) -> OrderRecord:
        _required_text(intent_id, "intent_id")
        row = self._fetch_one(
            "SELECT * FROM order_lifecycle_orders WHERE intent_id=?", (intent_id,)
        )
        if row is None:
            raise KeyError(intent_id)
        return self._record(row)

    def list_reconciliation_required(self) -> tuple[OrderRecord, ...]:
        cursor = self.connection.execute(
            """SELECT * FROM order_lifecycle_orders
               WHERE reconciliation_required=1 ORDER BY timestamp, intent_id"""
        )
        return tuple(
            self._record(self._row_mapping(row, cursor.description))
            for row in cursor.fetchall()
        )

    def apply(self, intent_id: str, event: LifecycleEvent) -> OrderRecord:
        """Persist one lifecycle fact atomically; this never sends or cancels orders."""
        _required_text(intent_id, "intent_id")
        if not isinstance(event, LifecycleEvent):
            raise TypeError("typed LifecycleEvent required")
        payload = self._event_payload(event)
        with self._transaction():
            prior_event = self._fetch_one(
                """SELECT payload FROM order_lifecycle_events
                   WHERE intent_id=? AND event_id=?""",
                (intent_id, event.event_id),
            )
            if prior_event is not None:
                if prior_event["payload"] != payload:
                    raise IdempotencyConflict("event_id was already used for different event data")
                return self.get(intent_id)

            record = self.get(intent_id)
            state, filled, realized, venue_id, reconcile = self._transition(record, event)
            revision = record.revision + 1
            self.connection.execute(
                """UPDATE order_lifecycle_orders SET
                    venue_order_id=?, filled_quantity=?, realized_pnl=?, state=?,
                    reconciliation_required=?, updated_at=?, revision=?
                   WHERE intent_id=? AND revision=?""",
                (venue_id, str(filled), str(realized), state.value, int(reconcile),
                 event.timestamp, revision, intent_id, record.revision),
            )
            self.connection.execute(
                """INSERT INTO order_lifecycle_events
                   (intent_id, event_id, payload, timestamp, resulting_revision)
                   VALUES (?, ?, ?, ?, ?)""",
                (intent_id, event.event_id, payload, event.timestamp, revision),
            )
            return self.get(intent_id)

    @staticmethod
    def _transition(
        record: OrderRecord, event: LifecycleEvent,
    ) -> tuple[OrderLifecycleState, Decimal, Decimal, str | None, bool]:
        state = record.state
        filled = record.filled_quantity
        realized = record.realized_pnl
        venue_id = record.venue_order_id
        reconcile = record.reconciliation_required
        action = event.action

        if action is LifecycleAction.PREFLIGHT_PASSED and state is OrderLifecycleState.CREATED:
            state = OrderLifecycleState.PREFLIGHTED
        elif action is LifecycleAction.PREFLIGHT_REJECTED and state is OrderLifecycleState.CREATED:
            state = OrderLifecycleState.REJECTED
        elif action is LifecycleAction.RESERVED and state is OrderLifecycleState.PREFLIGHTED:
            state = OrderLifecycleState.RESERVED
        elif action is LifecycleAction.ABORT and state in (
            OrderLifecycleState.PREFLIGHTED, OrderLifecycleState.RESERVED,
        ):
            state = OrderLifecycleState.REJECTED
        elif action is LifecycleAction.SUBMIT_INTENT and state is OrderLifecycleState.RESERVED:
            state = OrderLifecycleState.SUBMITTING
        elif action is LifecycleAction.ACKNOWLEDGED and state is OrderLifecycleState.SUBMITTING:
            if event.venue_order_id is None:
                raise InvalidLifecycleTransition("acknowledgment requires venue_order_id")
            venue_id = SQLiteOrderRepository._merge_venue_id(venue_id, event.venue_order_id)
            state = OrderLifecycleState.ACKNOWLEDGED
        elif action is LifecycleAction.FILL and state in (
            OrderLifecycleState.ACKNOWLEDGED, OrderLifecycleState.PARTIALLY_FILLED,
        ):
            if event.fill_quantity is None:
                raise InvalidLifecycleTransition("fill event requires fill_quantity")
            filled += event.fill_quantity
            if filled > record.quantity:
                raise InvalidLifecycleTransition("fill quantity exceeds the order quantity")
            state = (
                OrderLifecycleState.FILLED
                if filled == record.quantity else OrderLifecycleState.PARTIALLY_FILLED
            )
            realized += event.realized_pnl_delta or Decimal(0)
        elif action is LifecycleAction.CANCEL_INTENT and state in (
            OrderLifecycleState.ACKNOWLEDGED, OrderLifecycleState.PARTIALLY_FILLED,
        ):
            state = OrderLifecycleState.CANCEL_PENDING
        elif action is LifecycleAction.CANCELED and state is OrderLifecycleState.CANCEL_PENDING:
            state = OrderLifecycleState.CANCELED
        elif action is LifecycleAction.LOST_ACK and state is OrderLifecycleState.SUBMITTING:
            state, reconcile = OrderLifecycleState.UNKNOWN, True
        elif action is LifecycleAction.TIMEOUT and state is OrderLifecycleState.SUBMITTING:
            # A timeout after a transport handoff may hide a successful venue accept.
            state, reconcile = OrderLifecycleState.UNKNOWN, True
        elif action is LifecycleAction.TIMEOUT and state in (
            OrderLifecycleState.ACKNOWLEDGED,
            OrderLifecycleState.PARTIALLY_FILLED,
            OrderLifecycleState.CANCEL_PENDING,
        ):
            state, reconcile = OrderLifecycleState.TIMED_OUT, True
        elif action is LifecycleAction.RECONCILE and state in (
            OrderLifecycleState.UNKNOWN, OrderLifecycleState.TIMED_OUT,
        ):
            state, filled, realized, venue_id, reconcile = SQLiteOrderRepository._reconcile(
                record, event, venue_id, filled, realized,
            )
        elif action is LifecycleAction.SETTLE and state in (
            OrderLifecycleState.FILLED, OrderLifecycleState.CANCELED,
        ):
            if event.final_realized_pnl is None:
                raise InvalidLifecycleTransition("settlement requires final_realized_pnl")
            realized = event.final_realized_pnl
            state, reconcile = OrderLifecycleState.SETTLED, False
        else:
            raise InvalidLifecycleTransition(
                f"{action.value} is not valid while order is {state.value}"
            )
        return state, filled, realized, venue_id, reconcile

    @staticmethod
    def _reconcile(
        record: OrderRecord,
        event: LifecycleEvent,
        venue_id: str | None,
        filled: Decimal,
        realized: Decimal,
    ) -> tuple[OrderLifecycleState, Decimal, Decimal, str | None, bool]:
        observed = event.reconciled_state
        if observed is None:
            raise InvalidLifecycleTransition("reconciliation requires a recognized venue state")
        if event.venue_order_id is not None:
            venue_id = SQLiteOrderRepository._merge_venue_id(venue_id, event.venue_order_id)
        if event.cumulative_filled_quantity is not None:
            observed_filled = event.cumulative_filled_quantity
            if observed_filled < filled or observed_filled > record.quantity:
                raise InvalidLifecycleTransition("reconciled fill quantity is inconsistent")
            filled = observed_filled
        if event.realized_pnl_delta is not None:
            realized += event.realized_pnl_delta

        target_by_observation = {
            ReconciledOrderState.ACKNOWLEDGED: OrderLifecycleState.ACKNOWLEDGED,
            ReconciledOrderState.PARTIALLY_FILLED: OrderLifecycleState.PARTIALLY_FILLED,
            ReconciledOrderState.FILLED: OrderLifecycleState.FILLED,
            ReconciledOrderState.CANCELED: OrderLifecycleState.CANCELED,
            ReconciledOrderState.REJECTED: OrderLifecycleState.REJECTED,
        }
        if observed is ReconciledOrderState.NOT_FOUND:
            return OrderLifecycleState.UNKNOWN, filled, realized, venue_id, True
        target = target_by_observation[observed]
        if observed is ReconciledOrderState.PARTIALLY_FILLED and not (
            Decimal(0) < filled < record.quantity
        ):
            raise InvalidLifecycleTransition("partial reconciliation requires a partial fill quantity")
        if observed is ReconciledOrderState.FILLED and filled != record.quantity:
            raise InvalidLifecycleTransition("filled reconciliation requires the full quantity")
        if observed is ReconciledOrderState.ACKNOWLEDGED and filled != 0:
            raise InvalidLifecycleTransition("acknowledged reconciliation cannot erase known fills")
        if observed is ReconciledOrderState.REJECTED and filled != 0:
            raise InvalidLifecycleTransition("rejected reconciliation cannot include fills")
        return target, filled, realized, venue_id, False

    @staticmethod
    def _merge_venue_id(current: str | None, observed: str) -> str:
        if current is not None and current != observed:
            raise InvalidLifecycleTransition("venue_order_id cannot change once observed")
        return observed

    @staticmethod
    def _event_payload(event: LifecycleEvent) -> str:
        fields = {
            "action": event.action.value,
            "venue_order_id": event.venue_order_id,
            "fill_quantity": None if event.fill_quantity is None else str(event.fill_quantity),
            "realized_pnl_delta": (
                None if event.realized_pnl_delta is None else str(event.realized_pnl_delta)
            ),
            "final_realized_pnl": (
                None if event.final_realized_pnl is None else str(event.final_realized_pnl)
            ),
            "reconciled_state": (
                None if event.reconciled_state is None else event.reconciled_state.value
            ),
            "cumulative_filled_quantity": (
                None if event.cumulative_filled_quantity is None
                else str(event.cumulative_filled_quantity)
            ),
            "timestamp": event.timestamp,
        }
        return json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _matches_intent(record: OrderRecord, intent: OrderIntent) -> bool:
        return (
            record.intent_id == intent.intent_id
            and record.client_order_id == intent.client_order_id
            and record.route_id == intent.route_id
            and record.opportunity_id == intent.opportunity_id
            and record.timestamp == intent.timestamp
            and record.strategy == intent.strategy
            and record.allocation == intent.allocation
            and record.expected_pnl == intent.expected_pnl
            and record.quantity == intent.quantity
        )

    def _fetch_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        cursor = self.connection.execute(sql, params)
        row = cursor.fetchone()
        return None if row is None else self._row_mapping(row, cursor.description)

    @staticmethod
    def _row_mapping(
        row: Any, description: Any = None,
    ) -> dict[str, Any]:
        if isinstance(row, sqlite3.Row):
            return dict(row)
        if isinstance(row, dict):
            return row
        if description is None:
            raise TypeError("column metadata required for tuple rows")
        return dict(zip((column[0] for column in description), row))

    @staticmethod
    def _record(row: dict[str, Any]) -> OrderRecord:
        try:
            state = OrderLifecycleState(row["state"])
        except (KeyError, ValueError):
            raise RuntimeError("unrecognized persisted order state; refusing to proceed") from None
        return OrderRecord(
            intent_id=row["intent_id"],
            client_order_id=row["client_order_id"],
            venue_order_id=row["venue_order_id"],
            route_id=row["route_id"],
            opportunity_id=row["opportunity_id"],
            timestamp=row["timestamp"],
            strategy=row["strategy"],
            allocation=Decimal(row["allocation"]),
            expected_pnl=Decimal(row["expected_pnl"]),
            realized_pnl=Decimal(row["realized_pnl"]),
            quantity=Decimal(row["quantity"]),
            filled_quantity=Decimal(row["filled_quantity"]),
            state=state,
            reconciliation_required=bool(row["reconciliation_required"]),
            updated_at=row["updated_at"],
            revision=row["revision"],
        )

    def close(self) -> None:
        """Close only connections opened from this repository's injected path."""
        if self._owns_connection:
            self.connection.close()


class _Savepoint:
    """Minimal context manager with rollback-safe nested SQLite transactions."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.name = f"order_lifecycle_{id(self):x}"

    def __enter__(self):
        self.connection.execute(f"SAVEPOINT {self.name}")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_type is None:
            self.connection.execute(f"RELEASE SAVEPOINT {self.name}")
        else:
            self.connection.execute(f"ROLLBACK TO SAVEPOINT {self.name}")
            self.connection.execute(f"RELEASE SAVEPOINT {self.name}")
        return False
