"""Durable, append-only execution journal for one or more ARBX sessions."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


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

    def close(self) -> None:
        self.db.close()

