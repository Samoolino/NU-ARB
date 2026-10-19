"""Backend-neutral journal persistence; does not enable any execution pathway."""
from __future__ import annotations

import json
from typing import Any

from arbx.storage import StorageBackend


TRADE_COLUMNS = (
    "session_id", "mode", "exchange_a", "exchange_b", "strategy", "path", "symbols",
    "requested_quantity", "executed_quantity", "average_fill_price", "fees", "gross_pnl",
    "slippage", "net_pnl", "latency_ms", "book_age_ms", "execution_status",
    "verification_status", "risk_decision", "failure_reason", "target_before", "target_after",
    "cumulative_realized_pnl",
)
OPPORTUNITY_COLUMNS = (
    "session_id", "mode", "strategy", "exchange_a", "exchange_b", "symbol", "requested_usd",
    "expected_net_usd", "worst_case_net_usd", "expected_net_bps", "worst_case_net_bps",
    "book_age_ms", "decision", "rejection_reason", "evidence",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


class BackendTradeJournal:
    """Trade/evidence journal over StorageBackend.

    PostgreSQL mode requires migrations 001 to have been applied first. This class
    is deliberately persistence-only: it never submits orders or enables live mode.
    """

    def __init__(self, backend: StorageBackend):
        self.backend = backend
        self.dialect = backend.dialect
        if self.dialect not in {"sqlite", "postgres"}:
            raise ValueError("unsupported storage dialect")
        if self.dialect == "sqlite":
            self._initialize_sqlite()

    def _initialize_sqlite(self) -> None:
        with self.backend.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                mode TEXT NOT NULL, exchange_a TEXT NOT NULL, exchange_b TEXT,
                strategy TEXT NOT NULL, path TEXT NOT NULL DEFAULT '[]',
                symbols TEXT NOT NULL DEFAULT '[]', requested_quantity REAL,
                executed_quantity REAL, average_fill_price REAL, fees REAL, gross_pnl REAL,
                slippage REAL, net_pnl REAL NOT NULL, latency_ms REAL, book_age_ms REAL,
                execution_status TEXT NOT NULL, verification_status TEXT NOT NULL,
                risk_decision TEXT, failure_reason TEXT, target_before REAL, target_after REAL,
                cumulative_realized_pnl REAL NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS trades_session_idx ON trades(session_id, id)")
            db.execute("""CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                mode TEXT NOT NULL, strategy TEXT NOT NULL, exchange_a TEXT NOT NULL,
                exchange_b TEXT, symbol TEXT NOT NULL, requested_usd REAL NOT NULL,
                expected_net_usd REAL NOT NULL, worst_case_net_usd REAL NOT NULL,
                expected_net_bps REAL NOT NULL, worst_case_net_bps REAL NOT NULL,
                book_age_ms REAL NOT NULL, decision TEXT NOT NULL, rejection_reason TEXT,
                evidence TEXT NOT NULL DEFAULT '{}'
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS opportunities_session_idx ON opportunities(session_id, id)")
            db.execute("""CREATE TABLE IF NOT EXISTS venue_certifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                session_id TEXT, venue TEXT NOT NULL, symbol TEXT NOT NULL,
                notional_usd REAL NOT NULL, live_eligible INTEGER NOT NULL,
                reasons TEXT NOT NULL DEFAULT '[]', evidence TEXT NOT NULL DEFAULT '{}'
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS venue_certifications_venue_idx ON venue_certifications(venue, id)")

    def _placeholder(self) -> str:
        return "%s" if self.dialect == "postgres" else "?"

    def _json_parameter(self, value: Any) -> Any:
        encoded = json.dumps(value, separators=(",", ":"), allow_nan=False)
        if self.dialect == "postgres":
            from psycopg.types.json import Jsonb
            return Jsonb(json.loads(encoded))
        return encoded

    def _insert_id(self, table: str, columns: tuple[str, ...], values: list[Any]) -> int:
        p = self._placeholder()
        names = ", ".join(columns)
        marks = ", ".join(p for _ in columns)
        statement = f"INSERT INTO {table} ({names}) VALUES ({marks})"
        if self.dialect == "postgres":
            statement += " RETURNING id"
        with self.backend.transaction() as db:
            cursor = db.execute(statement, tuple(values))
            if self.dialect == "postgres":
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("journal insert did not return an id")
                return int(row["id"] if isinstance(row, dict) else row[0])
            return int(cursor.lastrowid)

    def realized(self, session_id: str) -> float:
        p = self._placeholder()
        with self.backend.transaction() as db:
            row = db.fetch_one(
                f"SELECT COALESCE(SUM(net_pnl), 0) AS pnl FROM trades WHERE session_id={p} AND execution_status='FILLED'",
                (session_id,),
            )
        return float(row["pnl"] if row else 0.0)

    def recent_trades(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent trades for exactly one session, newest first.

        Requiring a session identifier prevents this persistence helper from
        accidentally exposing a cross-session journal. Authorization and mapping
        an authenticated owner to their session IDs remain the API caller's job.
        """
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("trade history limit must be between 1 and 100")

        p = self._placeholder()
        columns = (
            "id, session_id, timestamp, mode, exchange_a, exchange_b, strategy, path, symbols, "
            "requested_quantity, executed_quantity, average_fill_price, fees, gross_pnl, slippage, "
            "net_pnl, latency_ms, book_age_ms, execution_status, verification_status, risk_decision, "
            "failure_reason, target_before, target_after, cumulative_realized_pnl"
        )
        with self.backend.transaction() as db:
            rows = db.fetch_all(
                f"SELECT {columns} FROM trades WHERE session_id={p} ORDER BY id DESC LIMIT {p}",
                (session_id, limit),
            )
        for row in rows:
            row["path"] = _json_value(row.get("path"))
            row["symbols"] = _json_value(row.get("symbols"))
        return rows

    def append(self, record: dict[str, Any]) -> int:
        values = [record.get(key) for key in TRADE_COLUMNS]
        values[5] = self._json_parameter(record.get("path", []))
        values[6] = self._json_parameter(record.get("symbols", []))
        return self._insert_id("trades", TRADE_COLUMNS, values)

    def append_fenced(
        self, record: dict[str, Any], *, owner_user_id: str, worker_id: str,
        fencing_token: int, now_epoch: int,
    ) -> int:
        """Append a paper/simulation journal row under the current worker fence.

        Ownership, phase, lease expiry and fencing token are checked in the same
        database transaction as the insert. A separate preflight lease check is
        deliberately insufficient because takeover could occur between check
        and write. Callers must use this method for worker-originated journal rows.
        """
        if not isinstance(record, dict):
            raise ValueError("journal record is required")
        if not isinstance(owner_user_id, str) or not owner_user_id.strip():
            raise ValueError("owner_user_id is required")
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id is required")
        if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token < 1:
            raise ValueError("fencing_token must be a positive integer")
        if isinstance(now_epoch, bool) or not isinstance(now_epoch, int) or now_epoch < 0:
            raise ValueError("now_epoch must be a non-negative integer")
        session_id = record.get("session_id")
        mode = record.get("mode")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id is required")
        if mode not in {"paper", "simulation"}:
            raise ValueError("fenced journal writes require paper or simulation mode")

        p = self._placeholder()
        lock = " FOR UPDATE" if self.dialect == "postgres" else ""
        with self.backend.transaction() as db:
            session = db.fetch_one(
                "SELECT owner_user_id, mode, phase FROM control_api_engine_sessions "
                "WHERE session_id=" + p + lock,
                (session_id,),
            )
            lease = db.fetch_one(
                "SELECT worker_id, lease_expires_at, fencing_token "
                "FROM control_api_engine_worker_leases WHERE session_id=" + p + lock,
                (session_id,),
            )
            if (
                session is None
                or session["owner_user_id"] != owner_user_id
                or session["mode"] != mode
                or session["phase"] not in {"STARTING", "PREFLIGHTING", "RUNNING"}
                or lease is None
                or lease["worker_id"] != worker_id
                or lease["fencing_token"] != fencing_token
                or lease["lease_expires_at"] <= now_epoch
            ):
                raise PermissionError("journal write rejected: current worker fence is not valid")

            values = [record.get(key) for key in TRADE_COLUMNS]
            values[5] = self._json_parameter(record.get("path", []))
            values[6] = self._json_parameter(record.get("symbols", []))
            names = ", ".join(TRADE_COLUMNS)
            marks = ", ".join(p for _ in TRADE_COLUMNS)
            statement = f"INSERT INTO trades ({names}) VALUES ({marks})"
            if self.dialect == "postgres":
                statement += " RETURNING id"
            cursor = db.execute(statement, tuple(values))
            if self.dialect == "postgres":
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("fenced journal insert did not return an id")
                return int(row["id"] if isinstance(row, dict) else row[0])
            return int(cursor.lastrowid)

    def append_opportunity(self, record: dict[str, Any]) -> int:
        values = [record.get(key) for key in OPPORTUNITY_COLUMNS]
        values[-1] = self._json_parameter(record.get("evidence", {}))
        return self._insert_id("opportunities", OPPORTUNITY_COLUMNS, values)

    def _insert_fenced_evidence(
        self, *, table: str, columns: tuple[str, ...], values: list[Any],
        session_id: str, owner_user_id: str, worker_id: str,
        fencing_token: int, now_epoch: int, expected_mode: str | None = None,
    ) -> int:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id is required")
        if not isinstance(owner_user_id, str) or not owner_user_id.strip():
            raise ValueError("owner_user_id is required")
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id is required")
        if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token < 1:
            raise ValueError("fencing_token must be a positive integer")
        if isinstance(now_epoch, bool) or not isinstance(now_epoch, int) or now_epoch < 0:
            raise ValueError("now_epoch must be a non-negative integer")

        p = self._placeholder()
        lock = " FOR UPDATE" if self.dialect == "postgres" else ""
        with self.backend.transaction() as db:
            session = db.fetch_one(
                "SELECT owner_user_id, mode, phase FROM control_api_engine_sessions "
                "WHERE session_id=" + p + lock, (session_id,),
            )
            lease = db.fetch_one(
                "SELECT worker_id, lease_expires_at, fencing_token "
                "FROM control_api_engine_worker_leases WHERE session_id=" + p + lock,
                (session_id,),
            )
            if (
                session is None
                or session["owner_user_id"] != owner_user_id
                or session["mode"] not in {"paper", "simulation"}
                or (expected_mode is not None and session["mode"] != expected_mode)
                or session["phase"] not in {"STARTING", "PREFLIGHTING", "RUNNING"}
                or lease is None
                or lease["worker_id"] != worker_id
                or lease["fencing_token"] != fencing_token
                or lease["lease_expires_at"] <= now_epoch
            ):
                raise PermissionError("journal write rejected: current worker fence is not valid")

            names = ", ".join(columns)
            marks = ", ".join(p for _ in columns)
            statement = f"INSERT INTO {table} ({names}) VALUES ({marks})"
            if self.dialect == "postgres":
                statement += " RETURNING id"
            cursor = db.execute(statement, tuple(values))
            if self.dialect == "postgres":
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("fenced journal insert did not return an id")
                return int(row["id"] if isinstance(row, dict) else row[0])
            return int(cursor.lastrowid)

    def append_opportunity_fenced(
        self, record: dict[str, Any], *, owner_user_id: str, worker_id: str,
        fencing_token: int, now_epoch: int,
    ) -> int:
        if not isinstance(record, dict):
            raise ValueError("opportunity record is required")
        mode = record.get("mode")
        if mode not in {"paper", "simulation"}:
            raise ValueError("fenced opportunity writes require paper or simulation mode")
        values = [record.get(key) for key in OPPORTUNITY_COLUMNS]
        values[-1] = self._json_parameter(record.get("evidence", {}))
        return self._insert_fenced_evidence(
            table="opportunities", columns=OPPORTUNITY_COLUMNS, values=values,
            session_id=record.get("session_id"), owner_user_id=owner_user_id,
            worker_id=worker_id, fencing_token=fencing_token, now_epoch=now_epoch,
            expected_mode=mode,
        )

    def record_certification_fenced(
        self, *, session_id: str, venue: str, symbol: str, notional_usd: float,
        live_eligible: bool, reasons: list[str] | tuple[str, ...], evidence: dict[str, Any],
        owner_user_id: str, worker_id: str, fencing_token: int, now_epoch: int,
    ) -> int:
        columns = ("session_id", "venue", "symbol", "notional_usd", "live_eligible", "reasons", "evidence")
        values: list[Any] = [
            session_id, venue, symbol, notional_usd, live_eligible,
            self._json_parameter(list(reasons)), self._json_parameter(evidence),
        ]
        if self.dialect == "sqlite":
            values[4] = int(live_eligible)
        return self._insert_fenced_evidence(
            table="venue_certifications", columns=columns, values=values,
            session_id=session_id, owner_user_id=owner_user_id, worker_id=worker_id,
            fencing_token=fencing_token, now_epoch=now_epoch,
        )

    def recent_opportunities(self, limit: int = 25) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("opportunity history limit must be between 1 and 100")
        p = self._placeholder()
        with self.backend.transaction() as db:
            rows = db.fetch_all(
                """SELECT id, session_id, timestamp, mode, strategy, exchange_a, exchange_b, symbol,
                          requested_usd, expected_net_usd, worst_case_net_usd, expected_net_bps,
                          worst_case_net_bps, book_age_ms, decision, rejection_reason, evidence
                   FROM opportunities ORDER BY id DESC LIMIT """ + p,
                (limit,),
            )
        for row in rows:
            row["evidence"] = _json_value(row.get("evidence"))
        return rows

    def record_certification(
        self, *, session_id: str | None, venue: str, symbol: str, notional_usd: float,
        live_eligible: bool, reasons: list[str] | tuple[str, ...], evidence: dict[str, Any],
    ) -> int:
        columns = ("session_id", "venue", "symbol", "notional_usd", "live_eligible", "reasons", "evidence")
        reasons_json = self._json_parameter(list(reasons))
        evidence_json = self._json_parameter(evidence)
        values: list[Any] = [session_id, venue, symbol, notional_usd, live_eligible, reasons_json, evidence_json]
        if self.dialect == "sqlite":
            values[4] = int(live_eligible)
        return self._insert_id("venue_certifications", columns, values)

    def latest_certification(self, venue: str) -> dict[str, Any] | None:
        p = self._placeholder()
        with self.backend.transaction() as db:
            row = db.fetch_one(
                f"SELECT * FROM venue_certifications WHERE venue={p} ORDER BY id DESC LIMIT 1",
                (venue,),
            )
        if row is None:
            return None
        row["live_eligible"] = bool(row["live_eligible"])
        row["reasons"] = _json_value(row.get("reasons"))
        row["evidence"] = _json_value(row.get("evidence"))
        return row

    def close(self) -> None:
        """Compatibility no-op; connections are scoped to each transaction."""
        return None
