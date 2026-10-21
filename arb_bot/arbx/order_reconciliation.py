"""SQLite order intent ledger. Unknown/partial executions block subsequent engagements."""
import json, sqlite3, time, uuid
from pathlib import Path


class OrderLedger:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,timeout=10.0); self.db.row_factory=sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS executions(execution_id TEXT PRIMARY KEY,state TEXT NOT NULL,symbol TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,detail TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS order_legs(execution_id TEXT NOT NULL,leg_key TEXT NOT NULL,venue TEXT NOT NULL,client_order_id TEXT NOT NULL,order_id TEXT,side TEXT NOT NULL,requested REAL NOT NULL,filled REAL NOT NULL DEFAULT 0,status TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(execution_id,leg_key),UNIQUE(venue,client_order_id));
        CREATE INDEX IF NOT EXISTS execution_state_idx ON executions(state);
        """); self.db.commit()

    def unresolved(self):
        rows=self.db.execute("SELECT execution_id,state,symbol FROM executions WHERE state IN ('SUBMITTING','UNKNOWN','PARTIALLY_FILLED','RECONCILIATION_REQUIRED') ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def begin(self,symbol,legs):
        if self.unresolved(): raise RuntimeError("unresolved prior execution exists; reconcile before new orders")
        eid=uuid.uuid4().hex; now=time.time()
        with self.db:
            self.db.execute("INSERT INTO executions(execution_id,state,symbol,created_at,updated_at) VALUES(?,?,?,?,?)",(eid,"SUBMITTING",symbol,now,now))
            for leg in legs: self.add_leg(eid,leg)
        return eid

    def add_leg(self,eid,leg):
        with self.db:
            self.db.execute("INSERT INTO order_legs(execution_id,leg_key,venue,client_order_id,side,requested,status) VALUES(?,?,?,?,?,?,?)",
                (eid,leg["key"],leg["venue"],leg["client_order_id"],leg["side"],float(leg["requested"]),"SUBMITTING"))

    def update_leg(self,eid,key,*,status,order=None,error=None,requested=None):
        order=order or {}; detail={"error":error} if error else {}
        detail.update({k:order.get(k) for k in ("status","filled","remaining","average","cost","clientOrderId") if k in order})
        with self.db:
            self.db.execute("UPDATE order_legs SET order_id=COALESCE(?,order_id),requested=COALESCE(?,requested),filled=?,status=?,detail=? WHERE execution_id=? AND leg_key=?",
                (str(order["id"]) if order.get("id") is not None else None,
                 float(requested) if requested is not None else None,
                 float(order.get("filled") or 0),status,json.dumps(detail),eid,key))
            self.db.execute("UPDATE executions SET updated_at=? WHERE execution_id=?",(time.time(),eid))

    def set_state(self,eid,state,detail=None):
        with self.db:
            self.db.execute("UPDATE executions SET state=?,updated_at=?,detail=? WHERE execution_id=?",(state,time.time(),json.dumps(detail or {}),eid))


    def get_execution(self, eid):
        """Return one execution and its legs for operator reconciliation."""
        row = self.db.execute(
            "SELECT execution_id,state,symbol,created_at,updated_at,detail FROM executions WHERE execution_id=?",
            (eid,),
        ).fetchone()
        if row is None:
            return None
        execution = dict(row)
        execution["detail"] = json.loads(execution["detail"] or "{}")
        legs = self.db.execute(
            "SELECT leg_key,venue,client_order_id,order_id,side,requested,filled,status,detail "
            "FROM order_legs WHERE execution_id=? ORDER BY rowid",
            (eid,),
        ).fetchall()
        execution["legs"] = []
        for leg in legs:
            item = dict(leg)
            item["detail"] = json.loads(item["detail"] or "{}")
            execution["legs"].append(item)
        return execution

    def resolve_manual(self, eid, *, operator, evidence, confirm=False):
        """Close a halted execution only after a human has reconciled venue state.

        This records an auditable acknowledgement; it does not verify the evidence,
        query an exchange, or make inventory safe. Callers must inspect balances,
        order/trade history, and residual assets before invoking it.
        """
        operator = str(operator or "").strip()
        evidence = str(evidence or "").strip()
        if confirm is not True:
            raise ValueError("explicit confirm=True is required")
        if not operator or not evidence:
            raise ValueError("operator and reconciliation evidence are required")
        with self.db:
            row = self.db.execute(
                "SELECT state,detail FROM executions WHERE execution_id=?", (eid,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown execution: {eid}")
            if row["state"] not in (
                "SUBMITTING", "UNKNOWN", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED"
            ):
                raise ValueError(f"execution is not unresolved: {row['state']}")
            detail = json.loads(row["detail"] or "{}")
            detail["manual_resolution"] = {
                "operator": operator,
                "evidence": evidence,
                "resolved_at": time.time(),
                "previous_state": row["state"],
                "acknowledgement_only": True,
            }
            self.db.execute(
                "UPDATE executions SET state='RESOLVED_MANUALLY',updated_at=?,detail=? "
                "WHERE execution_id=?",
                (time.time(), json.dumps(detail), eid),
            )

    def close(self): self.db.close()
