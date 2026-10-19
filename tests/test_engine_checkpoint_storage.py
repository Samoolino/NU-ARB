from __future__ import annotations

import pytest

from arbx.control_store import ControlPlaneStore
from arbx.storage import SQLiteBackend


def make_store(path):
    store = ControlPlaneStore(SQLiteBackend(path))
    store.initialize()
    store.create_user(user_id="checkpoint-owner", email="checkpoint@example.test", salt=b"salt", password_hash=b"hash", created_at="2026-01-01T00:00:00Z")
    store.register_engine_session(session_id="checkpoint-session", owner_user_id="checkpoint-owner", mode="simulation", phase="CREATED", created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
    assert store.update_engine_session(session_id="checkpoint-session", owner_user_id="checkpoint-owner", phase="STARTING", updated_at="2026-01-01T00:00:01Z")
    lease = store.claim_engine_worker_lease(session_id="checkpoint-session", worker_id="worker-a", now_epoch=100, lease_seconds=30)
    assert lease is not None
    assert store.transition_engine_session_with_lease(session_id="checkpoint-session", owner_user_id="checkpoint-owner", worker_id="worker-a", fencing_token=lease["fencing_token"], phase="RUNNING", updated_at="2026-01-01T00:00:02Z", now_epoch=101)
    return store, lease


def test_checkpoint_is_saved_and_verified_under_current_fence(tmp_path):
    store, lease = make_store(tmp_path / "checkpoint.sqlite3")
    saved = store.save_engine_checkpoint_fenced(session_id="checkpoint-session", owner_user_id="checkpoint-owner", worker_id="worker-a", fencing_token=lease["fencing_token"], now_epoch=102, updated_at="2026-01-01T00:00:03Z", checkpoint={"step": 4, "cursor": "BTC/USDT"}, expected_version=0)
    assert saved["checkpoint_version"] == 1
    loaded = store.get_engine_checkpoint(session_id="checkpoint-session", owner_user_id="checkpoint-owner")
    assert loaded["checkpoint"] == {"step": 4, "cursor": "BTC/USDT"}
    assert loaded["checkpoint_sha256"] == saved["checkpoint_sha256"]


def test_checkpoint_rejects_expired_or_stale_worker_fence(tmp_path):
    store, lease = make_store(tmp_path / "stale.sqlite3")
    result = store.save_engine_checkpoint_fenced(session_id="checkpoint-session", owner_user_id="checkpoint-owner", worker_id="worker-a", fencing_token=lease["fencing_token"], now_epoch=131, updated_at="2026-01-01T00:00:03Z", checkpoint={"step": 1}, expected_version=0)
    assert result is None


def test_checkpoint_uses_optimistic_version_to_prevent_lost_updates(tmp_path):
    store, lease = make_store(tmp_path / "version.sqlite3")
    args = dict(session_id="checkpoint-session", owner_user_id="checkpoint-owner", worker_id="worker-a", fencing_token=lease["fencing_token"], now_epoch=102, updated_at="2026-01-01T00:00:03Z")
    store.save_engine_checkpoint_fenced(**args, checkpoint={"step": 1}, expected_version=0)
    with pytest.raises(ValueError, match="checkpoint version conflict"):
        store.save_engine_checkpoint_fenced(**args, checkpoint={"step": 2}, expected_version=0)


def test_checkpoint_is_not_readable_by_another_owner(tmp_path):
    store, lease = make_store(tmp_path / "owner.sqlite3")
    store.save_engine_checkpoint_fenced(session_id="checkpoint-session", owner_user_id="checkpoint-owner", worker_id="worker-a", fencing_token=lease["fencing_token"], now_epoch=102, updated_at="2026-01-01T00:00:03Z", checkpoint={"step": 1}, expected_version=0)
    assert store.get_engine_checkpoint(session_id="checkpoint-session", owner_user_id="other-owner") is None


def test_postgres_checkpoint_migration_has_fence_and_version_columns():
    from pathlib import Path
    sql = (Path(__file__).parents[1] / "migrations" / "postgres" / "008_engine_checkpoints.sql").read_text(encoding="utf-8")
    for column in ("checkpoint_version", "checkpoint_json", "checkpoint_sha256", "worker_id", "fencing_token"):
        assert column in sql


def test_runtime_checkpoint_helpers_keep_writes_fenced(tmp_path):
    from arbx.engine_runtime import DurableEngineRuntime
    store, lease = make_store(tmp_path / "runtime-checkpoint.sqlite3")
    runtime = DurableEngineRuntime(store, worker_id="worker-a", lease_seconds=30)
    saved = runtime.persist_checkpoint(owner_user_id="checkpoint-owner", session_id="checkpoint-session", lease=lease, checkpoint={"step": 9}, expected_version=0, updated_at="2026-01-01T00:00:03Z", now_epoch=102)
    assert saved["checkpoint_version"] == 1
    assert runtime.load_checkpoint(owner_user_id="checkpoint-owner", session_id="checkpoint-session")["checkpoint"] == {"step": 9}
    stale = {**lease, "fencing_token": lease["fencing_token"] + 1}
    assert runtime.persist_checkpoint(owner_user_id="checkpoint-owner", session_id="checkpoint-session", lease=stale, checkpoint={"step": 10}, expected_version=1, updated_at="2026-01-01T00:00:04Z", now_epoch=103) is None


def test_checkpoint_load_detects_checksum_tampering(tmp_path):
    store, lease = make_store(tmp_path / "tampered.sqlite3")
    store.save_engine_checkpoint_fenced(session_id="checkpoint-session", owner_user_id="checkpoint-owner", worker_id="worker-a", fencing_token=lease["fencing_token"], now_epoch=102, updated_at="2026-01-01T00:00:03Z", checkpoint={"step": 1}, expected_version=0)
    with store.backend.transaction() as db:
        db.execute("UPDATE control_api_engine_checkpoints SET checkpoint_json=? WHERE session_id=?", ('{"step":99}', "checkpoint-session"))
    with pytest.raises(ValueError, match="checksum"):
        store.get_engine_checkpoint(session_id="checkpoint-session", owner_user_id="checkpoint-owner")
