"""Lease-fenced durable engine lifecycle primitives.

This module deliberately does not start exchange adapters or submit orders. The
FastAPI lifecycle endpoints remain closed until runtime recovery and all release
gates have been verified.
"""
from __future__ import annotations

from typing import Any


class DurableEngineRuntime:
    """Coordinate durable paper/simulation lifecycle state with a worker lease."""

    def __init__(self, store, *, worker_id: str, lease_seconds: int = 30):
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id is required")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        self.store = store
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def create_session(
        self, *, owner_user_id: str, session_id: str, mode: str,
        now_iso: str, now_epoch: int, metadata_json: str = "{}",
    ) -> dict[str, Any]:
        """Create a durable session and claim its initial worker lease.

        Session creation and lease acquisition are separate transactions. If a
        process dies between them, the persisted CREATED session is safe to
        reconcile; callers must not report the engine as running until a
        fenced transition succeeds.
        """
        self.store.register_engine_session(
            session_id=session_id, owner_user_id=owner_user_id, mode=mode,
            phase="CREATED", created_at=now_iso, updated_at=now_iso,
            metadata_json=metadata_json,
        )
        lease = self.store.claim_engine_worker_lease(
            session_id=session_id, worker_id=self.worker_id,
            now_epoch=now_epoch, lease_seconds=self.lease_seconds,
        )
        if lease is None:
            self.store.update_engine_session(
                session_id=session_id, owner_user_id=owner_user_id,
                phase="FAILED", updated_at=now_iso, error_code="WORKER_LEASE_UNAVAILABLE",
            )
            raise RuntimeError("Could not acquire engine worker lease")
        if not self.store.transition_engine_session_with_lease(
            session_id=session_id, owner_user_id=owner_user_id,
            worker_id=self.worker_id, fencing_token=lease["fencing_token"],
            phase="STARTING", updated_at=now_iso, now_epoch=now_epoch,
        ):
            raise RuntimeError("Initial engine lifecycle transition lost its worker lease")
        return lease

    def heartbeat(self, *, owner_user_id: str, session_id: str, now_epoch: int) -> dict[str, Any] | None:
        """Renew a lease or acquire an expired lease for controlled recovery."""
        engine = self.store.get_engine_session(session_id)
        if engine is None or engine["owner_user_id"] != owner_user_id:
            return None
        if engine["phase"] in {"STOPPED", "FAILED", "PREFLIGHT_FAILED"}:
            return None
        return self.store.claim_engine_worker_lease(
            session_id=session_id, worker_id=self.worker_id,
            now_epoch=now_epoch, lease_seconds=self.lease_seconds,
        )

    def transition(
        self, *, owner_user_id: str, session_id: str, lease: dict[str, Any],
        phase: str, updated_at: str, now_epoch: int,
        error_code: str | None = None, metadata_json: str | None = None,
    ) -> bool:
        """Change durable lifecycle state only while this exact fence is current."""
        if lease.get("session_id") != session_id or lease.get("worker_id") != self.worker_id:
            return False
        return self.store.transition_engine_session_with_lease(
            session_id=session_id, owner_user_id=owner_user_id,
            worker_id=self.worker_id, fencing_token=lease.get("fencing_token"),
            phase=phase, updated_at=updated_at, now_epoch=now_epoch,
            error_code=error_code, metadata_json=metadata_json,
        )
