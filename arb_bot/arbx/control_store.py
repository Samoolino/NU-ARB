"""Backend-neutral persistence for control-plane identity and credential metadata.

This store is introduced independently from the FastAPI routes. Route cutover
must happen only after contract/integration tests and migration rehearsals pass.
"""
from __future__ import annotations

from typing import Any

from arbx.storage import StorageBackend


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
            """CREATE TABLE IF NOT EXISTS control_api_sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL
                REFERENCES control_api_users(id) ON DELETE CASCADE, expires_at INTEGER NOT NULL)""",
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

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.backend.transaction() as session:
            return session.fetch_one(self._sql(
                "SELECT id,email,salt,password_hash,created_at FROM control_api_users WHERE email=?"
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
                   FROM control_api_exchange_credentials WHERE user_id=?"""
            ), (user_id,))

    def delete_credentials(self, user_id: str, exchange_id: str) -> None:
        with self.backend.transaction() as session:
            session.execute(self._sql(
                "DELETE FROM control_api_exchange_credentials WHERE user_id=? AND exchange_id=?"
            ), (user_id, exchange_id))
