"""Single-iteration supervisor for lease-fenced paper/simulation workers.

This module never submits live orders. The callbacks that perform side effects
must enforce the supplied fencing token at the persistence boundary; a lease
check before invoking a callback is not a substitute for transactional fencing.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from arbx.engine_runtime import recovery_disposition


class SupervisedPaperWorker:
    """Run at most one paper step after verifying ownership and recovery safety."""

    def __init__(self, *, runtime: Any, store: Any):
        self.runtime = runtime
        self.store = store

    def run_once(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        now_epoch: int,
        checkpoint_verified: Callable[[dict[str, Any]], bool],
        execute_step: Callable[[dict[str, Any], dict[str, Any]], Any],
        persist_checkpoint_fenced: Callable[[dict[str, Any], dict[str, Any]], Any],
    ) -> str:
        """Execute one bounded step, passing the current lease to fenced callbacks.

        The caller should invoke this method from an external supervisor loop
        that controls sleep, shutdown, process signals and bounded retries.
        """
        session = self.store.get_engine_session(session_id)
        if session is None or session.get("owner_user_id") != owner_user_id:
            return "NOT_FOUND"

        phase = session.get("phase")
        if phase in {"STOPPED", "FAILED", "PREFLIGHT_FAILED"}:
            return "IGNORE_TERMINAL"
        if phase != "RUNNING":
            try:
                return recovery_disposition(session, checkpoint_verified=False)
            except (TypeError, ValueError):
                return "FAIL_UNSAFE_RECOVERY"

        try:
            verified = checkpoint_verified(session)
        except Exception:
            return "FAIL_UNSAFE_RECOVERY"
        if verified is not True:
            return "FAIL_UNSAFE_RECOVERY"

        lease = self.runtime.heartbeat(
            owner_user_id=owner_user_id,
            session_id=session_id,
            now_epoch=now_epoch,
        )
        if lease is None:
            return "LEASE_LOST"
        if (
            lease.get("session_id") != session_id
            or not isinstance(lease.get("worker_id"), str)
            or isinstance(lease.get("fencing_token"), bool)
            or not isinstance(lease.get("fencing_token"), int)
            or lease["fencing_token"] < 1
        ):
            return "LEASE_INVALID"

        # The execution callback must check/fence every durable side effect.
        execute_step(session, lease)

        # Persist only through a caller-supplied atomic fenced checkpoint writer.
        # An exception is deliberately propagated so the process supervisor can
        # stop/restart the worker; it must not be treated as a successful step.
        persist_checkpoint_fenced(session, lease)
        return "STEP_COMPLETED"
