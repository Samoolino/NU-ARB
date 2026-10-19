"""Backend-neutral persistence for control-plane identity and credential metadata.

This store is introduced independently from the FastAPI routes. Route cutover
must happen only after contract/integration tests and migration rehearsals pass.
"""
from __future__ import annotations

from typing import Any

from arbx.storage import StorageBackend


ENGINE_SESSION_PHASES = frozenset({
    "CREATED", "STARTING", "PREFLIGHTING", "RUNNING", "STOPPING",
    "STOPPED", "FAILED", "PREFLIGHT_FAILED",
})
ENGINE_SESSION_TERMINAL_PHASES = frozenset({"STOPPED", "FAILED", "PREFLIGHT_FAILED"})
ENGINE_SESSION_TRANSITIONS = {
    "CREATED": frozenset({"STARTING", "STOPPING", "STOPPED", "FAILED"}),
    "STARTING": frozenset({"PREFLIGHTING", "RUNNING", "STOPPING", "STOPPED", "FAILED"}),
    "PREFLIGHTING": frozenset({"RUNNING", "STOPPING", "STOPPED", "PREFLIGHT_FAILED", "FAILED"}),
    "RUNNING": frozenset({"STOPPING", "STOPPED", "FAILED"}),
    "STOPPING": frozenset({"STOPPED", "FAILED"}),
    "STOPPED": frozenset(),
    "FAILED": frozenset(),
    "PREFLIGHT_FAILED": frozenset(),
}


class ControlPlaneStore:
    def __init__(self, backend: StorageBackend):
        self.backend = backend

    def _sql(self, statement: str) -> str:
        return statement if self.backend.dialect == "sqlite" else statement.replace("?", "%s")

    def initialize(self) -> None:
        if self.backend.dialect == "postgres":
            # PostgreSQL schema is versioned and must be applied by the migration runner.
            return
        statements = (
            """CREATE TABLE IF NOT EXISTS control_api_users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, salt BLOB NOT NULL,
                password_hash BLOB NOT NULL, created_at TEXT NOT NULL)""",
            "CREATE UNIQUE INDEX IF NOT EXISTS control_api_users_email_ci_idx ON control_api_users(lower(email))",
            """CREATE TABLE IF NOT EXISTS control_api_sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL
                REFERENCES control_api_users(id) ON DELETE CASCADE, expires_at INTEGER NOT NULL)""",

            """CREATE TABLE IF NOT EXISTS control_api_engine_sessions (
                session_id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL REFERENCES control_api_users(id) ON DELETE CASCADE,
                mode TEXT NOT NULL CHECK(mode IN ('paper','simulation')),
                phase TEXT NOT NULL CHECK(phase IN ('CREATED','STARTING','PREFLIGHTING','RUNNING','STOPPING','STOPPED','FAILED','PREFLIGHT_FAILED')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                stopped_at TEXT,
                error_code TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}')""",
            "CREATE INDEX IF NOT EXISTS control_api_engine_sessions_owner_created_idx ON control_api_engine_sessions(owner_user_id, created_at)",
            """CREATE TABLE IF NOT EXISTS control_api_engine_worker_leases (
                session_id TEXT PRIMARY KEY REFERENCES control_api_engine_sessions(session_id) ON DELETE CASCADE,
                worker_id TEXT NOT NULL,
                lease_expires_at INTEGER NOT NULL,
                fencing_token INTEGER NOT NULL CHECK(fencing_token >= 1))""",
            """CREATE TABLE IF NOT EXISTS control_api_exchange_credentials (
                user_id TEXT NOT NULL REFERENCES control_api_users(id) ON DELETE CASCADE,
                exchange_id TEXT NOT NULL, encrypted_credentials BLOB NOT NULL,
                auth_mode TEXT NOT NULL, state TEXT NOT NULL, last_verified TEXT,
                verification_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(user_id, exchange_id))""",
        )
        with self.backend.transaction() as session:
            for statement in statements:
                session.execute(statement)


    def register_engine_session(self, *, session_id: str, owner_user_id: str,
                                mode: str, phase: str, created_at: str,
                                updated_at: str, metadata_json: str = "{}") -> dict[str, Any]:
        """Persist session ownership and initial lifecycle state atomically."""
        if not isinstance(session_id, str) or not session_id.strip() or mode not in {"paper", "simulation"}:
            raise ValueError("engine session requires an ID and a non-live mode")
        if phase not in ENGINE_SESSION_PHASES:
            raise ValueError(f"invalid engine session phase: {phase}")
        with self.backend.transaction() as session:
            session.execute(self._sql(
                """INSERT INTO control_api_engine_sessions
                   (session_id, owner_user_id, mode, phase, created_at, updated_at, metadata_json)
                   VALUES(?,?,?,?,?,?,?)"""
            ), (session_id, owner_user_id, mode, phase, created_at, updated_at, metadata_json))
            row = session.fetch_one(self._sql(
                """SELECT session_id, owner_user_id, mode, phase, created_at, updated_at,
                          stopped_at, error_code, metadata_json
                   FROM control_api_engine_sessions WHERE session_id=?"""
            ), (session_id,))
            assert row is not None
            return row

    def get_engine_session(self, session_id: str) -> dict[str, Any] | None:
        with self.backend.transaction() as session:
            return session.fetch_one(self._sql(
                """SELECT session_id, owner_user_id, mode, phase, created_at, updated_at,
                          stopped_at, error_code, metadata_json
                   FROM control_api_engine_sessions WHERE session_id=?"""
            ), (session_id,))

    def list_engine_sessions(self, owner_user_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if not owner_user_id or not 1 <= limit <= 500:
            raise ValueError("owner and limit (1..500) are required")
        with self.backend.transaction() as session:
            return session.fetch_all(self._sql(
                """SELECT session_id, owner_user_id, mode, phase, created_at, updated_at,
                          stopped_at, error_code, metadata_json
                   FROM control_api_engine_sessions WHERE owner_user_id=?
                   ORDER BY created_at DESC, session_id ASC LIMIT ?"""
            ), (owner_user_id, limit))

    def update_engine_session(self, *, session_id: str, owner_user_id: str,
                              phase: str, updated_at: str, stopped_at: str | None = None,
                              error_code: str | None = None,
                              metadata_json: str | None = None) -> bool:
        """Update an owned session only along the defined lifecycle state machine.

        Missing or foreign session IDs return false. Invalid phases and illegal
        transitions raise ValueError. A PostgreSQL row lock serializes competing
        lifecycle updates.
        """
        if phase not in ENGINE_SESSION_PHASES:
            raise ValueError(f"invalid engine session phase: {phase}")
        with self.backend.transaction() as session:
            lock = " FOR UPDATE" if self.backend.dialect == "postgres" else ""
            current = session.fetch_one(self._sql(
                "SELECT phase FROM control_api_engine_sessions WHERE session_id=? AND owner_user_id=?" + lock
            ), (session_id, owner_user_id))
            if current is None:
                return False
            current_phase = current["phase"]
            if phase != current_phase and phase not in ENGINE_SESSION_TRANSITIONS.get(current_phase, frozenset()):
                raise ValueError(f"invalid engine session transition: {current_phase} -> {phase}")
            # Terminal timestamps are server-owned and immutable: a caller must
            # not set one before termination or rewrite it on an idempotent update.
            if phase in ENGINE_SESSION_TERMINAL_PHASES and current_phase != phase:
                stopped_at = updated_at
            else:
                stopped_at = None
            cursor = session.execute(self._sql(
                """UPDATE control_api_engine_sessions
                   SET phase=?, updated_at=?, stopped_at=COALESCE(?, stopped_at), error_code=COALESCE(?, error_code),
                       metadata_json=COALESCE(?, metadata_json)
                   WHERE session_id=? AND owner_user_id=?"""
            ), (phase, updated_at, stopped_at, error_code, metadata_json, session_id, owner_user_id))
            if cursor.rowcount != 1:
                return False
            if phase in ENGINE_SESSION_TERMINAL_PHASES and current_phase != phase:
                session.execute(self._sql(
                    "DELETE FROM control_api_engine_worker_leases WHERE session_id=?"
                ), (session_id,))
            return True

    def claim_engine_worker_lease(self, *, session_id: str, worker_id: str,
                                  now_epoch: int, lease_seconds: int) -> dict[str, Any] | None:
        """Atomically claim/renew a worker lease and return its fencing token.

        A competing worker cannot take a live lease. A takeover after expiry
        increments the fencing token, allowing downstream writes to reject stale
        workers. Callers must use unique worker IDs per process incarnation.
        """
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id is required")
        if isinstance(now_epoch, bool) or not isinstance(now_epoch, int) or now_epoch < 0:
            raise ValueError("now_epoch must be a non-negative integer")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        with self.backend.transaction() as session:
            lock = " FOR UPDATE" if self.backend.dialect == "postgres" else ""
            engine = session.fetch_one(self._sql(
                "SELECT phase FROM control_api_engine_sessions WHERE session_id=?" + lock
            ), (session_id,))
            if engine is None or engine["phase"] in ENGINE_SESSION_TERMINAL_PHASES:
                return None
            return session.fetch_one(self._sql(
                """INSERT INTO control_api_engine_worker_leases
                       (session_id, worker_id, lease_expires_at, fencing_token)
                   VALUES(?,?,?,1)
                   ON CONFLICT(session_id) DO UPDATE SET
                       fencing_token=CASE
                           WHEN control_api_engine_worker_leases.worker_id=excluded.worker_id
                           THEN control_api_engine_worker_leases.fencing_token
                           ELSE control_api_engine_worker_leases.fencing_token+1
                       END,
                       worker_id=excluded.worker_id,
                       lease_expires_at=excluded.lease_expires_at
                   WHERE control_api_engine_worker_leases.lease_expires_at<=?
                      OR control_api_engine_worker_leases.worker_id=?
                   RETURNING session_id, worker_id, lease_expires_at, fencing_token"""
            ), (session_id, worker_id, now_epoch + lease_seconds, now_epoch, worker_id))

    def release_engine_worker_lease(self, *, session_id: str, worker_id: str,
                                    fencing_token: int) -> bool:
        """Release only the lease still owned by this worker incarnation."""
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id is required")
        if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token < 1:
            raise ValueError("fencing_token must be a positive integer")
        with self.backend.transaction() as session:
            cursor = session.execute(self._sql(
                """DELETE FROM control_api_engine_worker_leases
                   WHERE session_id=? AND worker_id=? AND fencing_token=?"""
            ), (session_id, worker_id, fencing_token))
            return cursor.rowcount == 1

    def create_user(self, *, user_id: str, email: str, salt: bytes,
                    password_hash: bytes, created_at: str) -> dict[str, Any]:
        with self.backend.transaction() as session:
            session.execute(self._sql(
                "INSERT INTO control_api_users(id,email,salt,password_hash,created_at) VALUES(?,?,?,?,?)"
            ), (user_id, email.lower(), salt, password_hash, created_at))
            row = session.fetch_one(self._sql(
                "SELECT id,email,created_at FROM control_api_users WHERE id=?"
            ), (user_id,))
            assert row is not None
            return row

    def create_user_with_session(self, *, user_id: str, email: str, salt: bytes,
                                 password_hash: bytes, created_at: str,
                                 token_hash: str, expires_at: int) -> dict[str, Any]:
        """Atomically create an account and its initial hashed-token session.

        This avoids leaving an account without a usable initial session if session
        persistence fails. The caller must generate and retain the raw token only
        in memory for the response cookie; only its hash belongs in this store.
        """
        with self.backend.transaction() as session:
            session.execute(self._sql(
                "INSERT INTO control_api_users(id,email,salt,password_hash,created_at) VALUES(?,?,?,?,?)"
            ), (user_id, email.lower(), salt, password_hash, created_at))
            session.execute(self._sql(
                "INSERT INTO control_api_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)"
            ), (token_hash, user_id, expires_at))
            row = session.fetch_one(self._sql(
                "SELECT id,email,created_at FROM control_api_users WHERE id=?"
            ), (user_id,))
            assert row is not None
            return row

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.backend.transaction() as session:
            return session.fetch_one(self._sql(
                "SELECT id,email,salt,password_hash,created_at FROM control_api_users WHERE lower(email)=lower(?)"
            ), (email.lower(),))

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.backend.transaction() as session:
            return session.fetch_one(self._sql(
                "SELECT id,email,created_at FROM control_api_users WHERE id=?"
            ), (user_id,))

    def create_session(self, *, token_hash: str, user_id: str, expires_at: int) -> None:
        with self.backend.transaction() as session:
            session.execute(self._sql(
                "INSERT INTO control_api_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)"
            ), (token_hash, user_id, expires_at))

    def get_session(self, token_hash: str) -> dict[str, Any] | None:
        with self.backend.transaction() as session:
            return session.fetch_one(self._sql(
                "SELECT user_id,expires_at FROM control_api_sessions WHERE token_hash=?"
            ), (token_hash,))

    def get_valid_session(self, token_hash: str, *, now_epoch: int) -> dict[str, Any] | None:
        """Return a session only while its expiry is strictly in the future."""
        with self.backend.transaction() as session:
            return session.fetch_one(self._sql(
                "SELECT user_id,expires_at FROM control_api_sessions WHERE token_hash=? AND expires_at>?"
            ), (token_hash, now_epoch))

    def purge_expired_sessions(self, *, now_epoch: int) -> int:
        """Delete sessions that are expired at or before the supplied epoch."""
        with self.backend.transaction() as session:
            cursor = session.execute(self._sql(
                "DELETE FROM control_api_sessions WHERE expires_at<=?"
            ), (now_epoch,))
            return max(0, int(cursor.rowcount))

    def revoke_user_sessions(self, user_id: str) -> int:
        """Revoke every session for one user without affecting other accounts."""
        with self.backend.transaction() as session:
            cursor = session.execute(self._sql(
                "DELETE FROM control_api_sessions WHERE user_id=?"
            ), (user_id,))
            return max(0, int(cursor.rowcount))

    def delete_session(self, token_hash: str) -> None:
        with self.backend.transaction() as session:
            session.execute(self._sql(
                "DELETE FROM control_api_sessions WHERE token_hash=?"
            ), (token_hash,))

    def save_credentials(self, *, user_id: str, exchange_id: str,
                         encrypted_credentials: bytes, auth_mode: str, state: str,
                         last_verified: str | None, verification_json: str) -> None:
        if self.backend.dialect == "postgres":
            statement = """INSERT INTO control_api_exchange_credentials
                (user_id,exchange_id,encrypted_credentials,auth_mode,state,last_verified,verification_json)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,exchange_id) DO UPDATE SET
                encrypted_credentials=excluded.encrypted_credentials, auth_mode=excluded.auth_mode,
                state=excluded.state, last_verified=excluded.last_verified,
                verification_json=excluded.verification_json"""
        else:
            statement = """INSERT INTO control_api_exchange_credentials
                (user_id,exchange_id,encrypted_credentials,auth_mode,state,last_verified,verification_json)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,exchange_id) DO UPDATE SET
                encrypted_credentials=excluded.encrypted_credentials, auth_mode=excluded.auth_mode,
                state=excluded.state, last_verified=excluded.last_verified,
                verification_json=excluded.verification_json"""
        with self.backend.transaction() as session:
            session.execute(self._sql(statement), (
                user_id, exchange_id, encrypted_credentials, auth_mode, state,
                last_verified, verification_json,
            ))

    def get_credentials(self, user_id: str, exchange_id: str) -> dict[str, Any] | None:
        with self.backend.transaction() as session:
            return session.fetch_one(self._sql(
                """SELECT user_id,exchange_id,encrypted_credentials,auth_mode,state,last_verified,verification_json
                   FROM control_api_exchange_credentials WHERE user_id=? AND exchange_id=?"""
            ), (user_id, exchange_id))

    def list_credentials(self, user_id: str) -> list[dict[str, Any]]:
        with self.backend.transaction() as session:
            return session.fetch_all(self._sql(
                """SELECT exchange_id,auth_mode,state,last_verified,verification_json
                   FROM control_api_exchange_credentials WHERE user_id=? ORDER BY exchange_id ASC"""
            ), (user_id,))

    def delete_credentials(self, user_id: str, exchange_id: str) -> None:
        with self.backend.transaction() as session:
            session.execute(self._sql(
                "DELETE FROM control_api_exchange_credentials WHERE user_id=? AND exchange_id=?"
            ), (user_id, exchange_id))
