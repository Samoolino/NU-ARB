from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import pytest


def test_checkpoint_write_and_takeover_on_disposable_postgres():
    url = os.getenv("ARBX_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set ARBX_TEST_POSTGRES_URL for disposable PostgreSQL integration tests")
    if urlparse(url).hostname not in {"localhost", "127.0.0.1", "postgres"}:
        pytest.fail("checkpoint integration test only accepts a local/disposable PostgreSQL host")
    from arbx.control_store import ControlPlaneStore
    from arbx.postgres import run_postgres_migrations
    from arbx.storage import PostgresBackend
    run_postgres_migrations(url)
    store = ControlPlaneStore(PostgresBackend(url))
    suffix = uuid.uuid4().hex
    owner = f"checkpoint-owner-{suffix}"
    session_id = f"checkpoint-session-{suffix}"
    store.create_user(user_id=owner, email=f"{owner}@example.invalid", salt=b"salt", password_hash=b"hash", created_at="2026-10-19T00:00:00Z")
    try:
        store.register_engine_session(session_id=session_id, owner_user_id=owner, mode="simulation", phase="STARTING", created_at="2026-10-19T00:00:00Z", updated_at="2026-10-19T00:00:00Z")
        old = store.claim_engine_worker_lease(session_id=session_id, worker_id=f"old-{suffix}", now_epoch=100, lease_seconds=5)
        new = store.claim_engine_worker_lease(session_id=session_id, worker_id=f"new-{suffix}", now_epoch=105, lease_seconds=10)
        assert old is not None and new is not None and new["fencing_token"] > old["fencing_token"]
        assert store.save_engine_checkpoint_fenced(session_id=session_id, owner_user_id=owner, worker_id=old["worker_id"], fencing_token=old["fencing_token"], now_epoch=106, updated_at="2026-10-19T00:00:01Z", checkpoint={"step": -1}, expected_version=0) is None
        saved = store.save_engine_checkpoint_fenced(session_id=session_id, owner_user_id=owner, worker_id=new["worker_id"], fencing_token=new["fencing_token"], now_epoch=106, updated_at="2026-10-19T00:00:02Z", checkpoint={"step": 2}, expected_version=0)
        assert saved is not None and saved["checkpoint_version"] == 1
        assert store.get_engine_checkpoint(session_id=session_id, owner_user_id=owner)["checkpoint"] == {"step": 2}
    finally:
        with store.backend.transaction() as db:
            db.execute("DELETE FROM control_api_users WHERE id=%s", (owner,))
