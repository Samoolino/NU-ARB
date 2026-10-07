"""Durable, append-only execution journal for one or more ARBX sessions."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from arbx.risk_policy import IncidentFact, PolicyStage, incident_payload


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
        self.db.execute("""CREATE TABLE IF NOT EXISTS venue_certifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            session_id TEXT,
            venue TEXT NOT NULL,
            symbol TEXT NOT NULL,
            notional_usd REAL NOT NULL,
            live_eligible INTEGER NOT NULL,
            reasons TEXT NOT NULL DEFAULT '[]',
            evidence TEXT NOT NULL DEFAULT '{}'
        )""")
        self.db.execute("CREATE INDEX IF NOT EXISTS venue_certifications_venue_idx ON venue_certifications(venue, id)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS risk_policy_state (
            policy_id TEXT PRIMARY KEY,
            state TEXT NOT NULL
        )""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS risk_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            code TEXT NOT NULL,
            realized_pnl REAL,
            balances_match INTEGER,
            open_orders INTEGER,
            partial_fills INTEGER,
            stage TEXT NOT NULL
        )""")
        self.db.commit()

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

    def record_certification(self, *, session_id: str | None, venue: str, symbol: str,
                             notional_usd: float, live_eligible: bool,
                             reasons: list[str] | tuple[str, ...], evidence: dict[str, Any]) -> int:
        cursor = self.db.execute(
            """INSERT INTO venue_certifications
               (session_id, venue, symbol, notional_usd, live_eligible, reasons, evidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, venue, symbol, notional_usd, int(live_eligible),
             json.dumps(list(reasons), separators=(",", ":")),
             json.dumps(evidence, separators=(",", ":"), allow_nan=False)),
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def latest_certification(self, venue: str) -> dict[str, Any] | None:
        row = self.db.execute(
            """SELECT * FROM venue_certifications
               WHERE venue=? ORDER BY id DESC LIMIT 1""", (venue,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["reasons"] = json.loads(result["reasons"])
        result["evidence"] = json.loads(result["evidence"])
        return result

    def load_risk_policy_state(self) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT state FROM risk_policy_state WHERE policy_id='global'"
        ).fetchone()
        return None if row is None else json.loads(row["state"])

    def save_risk_policy_state(self, state: dict[str, Any]) -> None:
        if (not isinstance(state, dict)
                or state.get("stage") not in {stage.value for stage in PolicyStage}
                or state.get("reason") is not None
                and not isinstance(state.get("reason"), str)):
            raise ValueError("invalid risk policy state")
        encoded = json.dumps(state, separators=(",", ":"), allow_nan=False)
        self.db.execute(
            """INSERT INTO risk_policy_state (policy_id, state) VALUES ('global', ?)
               ON CONFLICT(policy_id) DO UPDATE SET state=excluded.state""",
            (encoded,),
        )
        self.db.commit()

    def record_risk_incident(self, incident: IncidentFact,
                             state: dict[str, Any]) -> None:
        if not isinstance(incident, IncidentFact):
            raise TypeError("typed sanitized risk incident required")
        payload = incident_payload(incident)
        self._record_risk_incident_atomic(payload, state)

    def _record_risk_incident_atomic(self, incident: dict[str, Any],
                                     state: dict[str, Any]) -> None:
        """Atomically append allow-listed incident facts and update persisted breaker state."""
        encoded_state = json.dumps(state, separators=(",", ":"), allow_nan=False)
        cursor = self.db.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            cursor.execute(
                """INSERT INTO risk_incidents
                   (code, realized_pnl, balances_match, open_orders, partial_fills, stage)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    incident["code"], incident["realized_pnl"],
                    None if incident["balances_match"] is None else int(incident["balances_match"]),
                    None if incident["open_orders"] is None else int(incident["open_orders"]),
                    None if incident["partial_fills"] is None else int(incident["partial_fills"]),
                    incident["stage"],
                ),
            )
            cursor.execute(
                """INSERT INTO risk_policy_state (policy_id, state) VALUES ('global', ?)
                   ON CONFLICT(policy_id) DO UPDATE SET state=excluded.state""",
                (encoded_state,),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def recent_risk_incidents(self, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("risk incident limit must be between 1 and 1000")
        rows = self.db.execute(
            """SELECT id, timestamp, code, realized_pnl, balances_match, open_orders,
                      partial_fills, stage
               FROM risk_incidents ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("balances_match", "open_orders", "partial_fills"):
                if item[key] is not None:
                    item[key] = bool(item[key])
            result.append(item)
        return result

    def close(self) -> None:
        self.db.close()
