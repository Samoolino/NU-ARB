"""Durable, append-only execution journal for one or more ARBX sessions."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    session_id: str
    opportunity_id: str
    expires_at: float


class ReservationManager:
    """Durable, atomic resource reservations used before live execution."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.execute("""CREATE TABLE IF NOT EXISTS reservations (
            reservation_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            opportunity_id TEXT NOT NULL,
            resource_key TEXT NOT NULL,
            amount REAL NOT NULL CHECK(amount > 0),
            resource_limit REAL NOT NULL CHECK(resource_limit > 0),
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','RELEASED','EXPIRED')),
            released_at REAL
        )""")
        self.db.execute("CREATE INDEX IF NOT EXISTS reservations_resource_idx ON reservations(resource_key, status, expires_at)")
        self.db.execute("CREATE INDEX IF NOT EXISTS reservations_session_idx ON reservations(session_id, status)")
        self.db.commit()

    def _expire(self, now: float) -> None:
        self.db.execute(
            "UPDATE reservations SET status='EXPIRED' WHERE status='ACTIVE' AND expires_at <= ?", (now,)
        )

    def acquire(self, *, session_id: str, opportunity_id: str,
                resources: list[tuple[str, float, float]], ttl_s: float,
                now: float | None = None) -> Reservation | None:
        """Atomically reserve every requested resource or none of them."""
        import time
        import uuid
        now = time.time() if now is None else now
        if ttl_s <= 0 or not resources:
            raise ValueError("reservation requires a positive TTL and at least one resource")
        if any(amount <= 0 or limit <= 0 or amount > limit for _, amount, limit in resources):
            return None
        if len({key for key, _, _ in resources}) != len(resources):
            raise ValueError("reservation resource keys must be unique")
        reservation_id = uuid.uuid4().hex
        expires_at = now + ttl_s
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self._expire(now)
            for key, amount, limit in resources:
                row = self.db.execute(
                    "SELECT COALESCE(SUM(amount),0) AS used FROM reservations "
                    "WHERE resource_key=? AND status='ACTIVE' AND expires_at > ?",
                    (key, now),
                ).fetchone()
                if float(row["used"]) + amount > limit + 1e-12:
                    self.db.rollback()
                    return None
            for key, amount, limit in resources:
                self.db.execute(
                    "INSERT INTO reservations(reservation_id,session_id,opportunity_id,resource_key,amount,resource_limit,created_at,expires_at,status) "
                    "VALUES(?,?,?,?,?,?,?,?, 'ACTIVE')",
                    (reservation_id, session_id, opportunity_id, key, amount, limit, now, expires_at),
                )
            self.db.commit()
            return Reservation(reservation_id, session_id, opportunity_id, expires_at)
        except Exception:
            self.db.rollback()
            raise

    def release(self, reservation_id: str, *, now: float | None = None) -> bool:
        import time
        now = time.time() if now is None else now
        cursor = self.db.execute(
            "UPDATE reservations SET status='RELEASED', released_at=? "
            "WHERE reservation_id=? AND status='ACTIVE'", (now, reservation_id),
        )
        self.db.commit()
        return cursor.rowcount > 0

    def recover_expired(self, *, now: float | None = None) -> int:
        import time
        now = time.time() if now is None else now
        self.db.execute("BEGIN IMMEDIATE")
        self._expire(now)
        changed = self.db.total_changes
        self.db.commit()
        return changed

    def active(self, *, session_id: str | None = None, now: float | None = None) -> list[dict]:
        import time
        now = time.time() if now is None else now
        self._expire(now)
        self.db.commit()
        if session_id is None:
            rows = self.db.execute("SELECT * FROM reservations WHERE status='ACTIVE' ORDER BY created_at").fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM reservations WHERE session_id=? AND status='ACTIVE' ORDER BY created_at", (session_id,)
            ).fetchall()
        return [dict(row) for row in rows]


class TradeJournal:
    """SQLite journal; only completed fills contribute to realized PnL."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            mode TEXT NOT NULL,
            exchange_a TEXT NOT NULL,
            exchange_b TEXT,
            strategy TEXT NOT NULL,
            path TEXT NOT NULL DEFAULT '[]',
            symbols TEXT NOT NULL DEFAULT '[]',
            requested_quantity REAL,
            executed_quantity REAL,
            average_fill_price REAL,
            fees REAL,
            gross_pnl REAL,
            slippage REAL,
            net_pnl REAL NOT NULL,
            latency_ms REAL,
            book_age_ms REAL,
            execution_status TEXT NOT NULL,
            verification_status TEXT NOT NULL,
            risk_decision TEXT,
            failure_reason TEXT,
            target_before REAL,
            target_after REAL,
            cumulative_realized_pnl REAL NOT NULL
        )""")
        self.db.execute("CREATE INDEX IF NOT EXISTS trades_session_idx ON trades(session_id, id)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            mode TEXT NOT NULL,
            strategy TEXT NOT NULL,
            exchange_a TEXT NOT NULL,
            exchange_b TEXT,
            symbol TEXT NOT NULL,
            requested_usd REAL NOT NULL,
            expected_net_usd REAL NOT NULL,
            worst_case_net_usd REAL NOT NULL,
            expected_net_bps REAL NOT NULL,
            worst_case_net_bps REAL NOT NULL,
            book_age_ms REAL NOT NULL,
            decision TEXT NOT NULL,
            rejection_reason TEXT,
            evidence TEXT NOT NULL DEFAULT '{}'
        )""")
        self.db.execute("CREATE INDEX IF NOT EXISTS opportunities_session_idx ON opportunities(session_id, id)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS execution_runs (
            execution_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            strategy TEXT NOT NULL,
            opportunity_id TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            error TEXT
        )""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS execution_legs (
            execution_id TEXT NOT NULL,
            leg_index INTEGER NOT NULL,
            exchange_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            requested_amount REAL NOT NULL,
            state TEXT NOT NULL,
            order_id TEXT,
            filled REAL,
            cost REAL,
            fee REAL,
            updated_at REAL NOT NULL,
            error TEXT,
            PRIMARY KEY(execution_id, leg_index)
        )""")
        self.db.execute("CREATE INDEX IF NOT EXISTS execution_runs_state_idx ON execution_runs(state, mode, updated_at)")
        self.db.execute("CREATE INDEX IF NOT EXISTS execution_legs_order_idx ON execution_legs(order_id)")
        self.db.commit()
        self.reservations = ReservationManager(self.db)

    def realized(self, session_id: str) -> float:
        row = self.db.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) AS pnl FROM trades WHERE session_id=? AND execution_status='FILLED'",
            (session_id,),
        ).fetchone()
        return float(row["pnl"])

    def append(self, record: dict[str, Any]) -> int:
        columns = (
            "session_id", "mode", "exchange_a", "exchange_b", "strategy", "path", "symbols",
            "requested_quantity", "executed_quantity", "average_fill_price", "fees", "gross_pnl", "slippage",
            "net_pnl", "latency_ms", "book_age_ms", "execution_status", "verification_status", "risk_decision",
            "failure_reason", "target_before", "target_after", "cumulative_realized_pnl",
        )
        values = [record.get(key) for key in columns]
        values[5] = json.dumps(record.get("path", []), separators=(",", ":"))
        values[6] = json.dumps(record.get("symbols", []), separators=(",", ":"))
        placeholders = ",".join("?" for _ in columns)
        cursor = self.db.execute(
            f"INSERT INTO trades ({','.join(columns)}) VALUES ({placeholders})", values,
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def append_opportunity(self, record: dict[str, Any]) -> int:
        columns = (
            "session_id", "mode", "strategy", "exchange_a", "exchange_b", "symbol", "requested_usd",
            "expected_net_usd", "worst_case_net_usd", "expected_net_bps", "worst_case_net_bps",
            "book_age_ms", "decision", "rejection_reason", "evidence",
        )
        values = [record.get(key) for key in columns]
        values[-1] = json.dumps(record.get("evidence", {}), separators=(",", ":"), allow_nan=False)
        placeholders = ",".join("?" for _ in columns)
        cursor = self.db.execute(
            f"INSERT INTO opportunities ({','.join(columns)}) VALUES ({placeholders})", values,
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def recent_opportunities(self, limit: int = 25) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("opportunity history limit must be between 1 and 100")
        rows = self.db.execute(
            """SELECT id, session_id, timestamp, mode, strategy, exchange_a, exchange_b, symbol, requested_usd,
                      expected_net_usd, worst_case_net_usd, expected_net_bps, worst_case_net_bps, book_age_ms,
                      decision, rejection_reason, evidence
               FROM opportunities ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        results = []
        for row in rows:
            record = dict(row)
            record["evidence"] = json.loads(record["evidence"])
            results.append(record)
        return results

    def create_execution(self, *, execution_id: str, session_id: str, mode: str,
                         strategy: str, opportunity_id: str, legs: list[dict],
                         now: float | None = None) -> None:
        import time
        now = time.time() if now is None else now
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "INSERT INTO execution_runs(execution_id,session_id,mode,strategy,opportunity_id,state,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (execution_id, session_id, mode, strategy, opportunity_id, "RESERVED", now, now),
            )
            for leg in legs:
                self.db.execute(
                    "INSERT INTO execution_legs(execution_id,leg_index,exchange_id,symbol,side,requested_amount,state,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (execution_id, leg["leg_index"], leg["exchange_id"], leg["symbol"], leg["side"],
                     leg["requested_amount"], "RESERVED", now),
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def transition_execution(self, execution_id: str, state: str, *,
                             error: str | None = None, now: float | None = None) -> None:
        import time
        now = time.time() if now is None else now
        allowed = {
            "RESERVED": {"SUBMITTING", "HALTED", "RELEASED"},
            "SUBMITTING": {"SUBMITTED", "LEG_FAILED", "HALTED"},
            "SUBMITTED": {"FILLED", "PARTIAL", "LEG_FAILED", "HALTED", "SETTLEMENT_PENDING"},
            "PARTIAL": {"SUBMITTING", "HALTED", "SETTLEMENT_PENDING"},
            "FILLED": {"SETTLEMENT_PENDING", "VERIFIED"},
            "LEG_FAILED": {"HALTED", "SETTLEMENT_PENDING", "RELEASED"},
            "SETTLEMENT_PENDING": {"VERIFIED", "HALTED"},
            "VERIFIED": set(),
            "HALTED": set(),
            "RELEASED": set(),
        }
        row = self.db.execute("SELECT state FROM execution_runs WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown execution {execution_id}")
        if state not in allowed.get(row["state"], set()):
            raise ValueError(f"invalid execution transition {row['state']} -> {state}")
        self.db.execute(
            "UPDATE execution_runs SET state=?, updated_at=?, error=? WHERE execution_id=?",
            (state, now, error, execution_id),
        )
        self.db.commit()

    def transition_leg(self, execution_id: str, leg_index: int, state: str, *,
                       order: dict | None = None, error: str | None = None,
                       now: float | None = None) -> None:
        import time
        now = time.time() if now is None else now
        row = self.db.execute(
            "SELECT state FROM execution_legs WHERE execution_id=? AND leg_index=?",
            (execution_id, leg_index),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown execution leg {execution_id}/{leg_index}")
        allowed = {
            "RESERVED": {"SUBMITTING", "SUBMITTED", "LEG_FAILED", "HALTED"},
            "SUBMITTING": {"SUBMITTED", "LEG_FAILED", "HALTED"},
            "SUBMITTED": {"FILLED", "PARTIAL", "LEG_FAILED", "HALTED"},
            "PARTIAL": {"FILLED", "LEG_FAILED", "HALTED"},
            "FILLED": {"SETTLEMENT_PENDING", "VERIFIED"},
            "LEG_FAILED": {"HALTED", "SETTLEMENT_PENDING"},
            "SETTLEMENT_PENDING": {"VERIFIED", "HALTED"},
            "VERIFIED": set(),
            "HALTED": set(),
        }
        if state not in allowed.get(row["state"], set()):
            raise ValueError(f"invalid leg transition {row['state']} -> {state}")
        fields = ["state=?", "updated_at=?", "error=?"]
        values: list = [state, now, error]
        if order is not None:
            fields += ["order_id=?", "filled=?", "cost=?", "fee=?"]
            fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
            fee = sum(float(f.get("cost") or 0.0) for f in fees if f)
            values += [order.get("id"), float(order.get("filled") or 0.0),
                       float(order.get("cost") or 0.0), fee]
        values += [execution_id, leg_index]
        self.db.execute(
            f"UPDATE execution_legs SET {','.join(fields)} WHERE execution_id=? AND leg_index=?",
            values,
        )
        self.db.commit()

    def reconcile_leg(self, execution_id: str, leg_index: int, order: dict, *, now: float | None = None) -> None:
        """Persist the latest exchange-authoritative order snapshot without changing lifecycle state."""
        import time
        now = time.time() if now is None else now
        row = self.db.execute(
            "SELECT 1 FROM execution_legs WHERE execution_id=? AND leg_index=?",
            (execution_id, leg_index),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown execution leg {execution_id}/{leg_index}")
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        fee = sum(float(f.get("cost") or 0.0) for f in fees if f)
        self.db.execute(
            "UPDATE execution_legs SET order_id=?, filled=?, cost=?, fee=?, updated_at=? WHERE execution_id=? AND leg_index=?",
            (order.get("id"), float(order.get("filled") or 0.0), float(order.get("cost") or 0.0), fee,
             now, execution_id, leg_index),
        )
        self.db.commit()

    def open_executions(self, *, mode: str | None = None) -> list[dict]:
        query = "SELECT * FROM execution_runs WHERE state NOT IN ('VERIFIED','RELEASED')"
        args: tuple = ()
        if mode is not None:
            query += " AND mode=?"
            args = (mode,)
        query += " ORDER BY updated_at"
        return [dict(row) for row in self.db.execute(query, args).fetchall()]


    def close(self) -> None:
        self.db.close()
