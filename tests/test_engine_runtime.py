from __future__ import annotations

import pytest

from arbx.control_store import ControlPlaneStore
from arbx.engine_runtime import DurableEngineRuntime
from arbx.storage import SQLiteBackend


def make_store(tmp_path):
    store = ControlPlaneStore(SQLiteBackend(tmp_path / "engine-runtime.sqlite3"))
    store.initialize()
    store.create_user(
        user_id="runtime-owner", email="runtime-owner@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )
    store.create_user(
        user_id="runtime-other", email="runtime-other@example.test", salt=b"salt",
        password_hash=b"hash", created_at="2026-10-19T00:00:00+00:00",
    )
    return store


def test_durable_runtime_creates_owned_session_and_initial_fence(tmp_path):
    store = make_store(tmp_path)
    runtime = DurableEngineRuntime(store, worker_id="worker-incarnation-a", lease_seconds=10)

    lease = runtime.create_session(
        owner_user_id="runtime-owner", session_id="runtime-session", mode="paper",
        now_iso="2026-10-19T00:00:00+00:00", now_epoch=100,
    )

    assert lease["fencing_token"] == 1
    session = store.get_engine_session("runtime-session")
    assert session["owner_user_id"] == "runtime-owner"
    assert session["mode"] == "paper"
    assert session["phase"] == "STARTING"


def test_durable_runtime_heartbeat_takeover_fences_stale_worker(tmp_path):
    store = make_store(tmp_path)
    worker_a = DurableEngineRuntime(store, worker_id="worker-incarnation-a", lease_seconds=10)
    lease_a = worker_a.create_session(
        owner_user_id="runtime-owner", session_id="runtime-session", mode="simulation",
        now_iso="2026-10-19T00:00:00+00:00", now_epoch=100,
    )
    worker_b = DurableEngineRuntime(store, worker_id="worker-incarnation-b", lease_seconds=10)

    assert worker_b.heartbeat(
        owner_user_id="runtime-owner", session_id="runtime-session", now_epoch=105,
    ) is None
    lease_b = worker_b.heartbeat(
        owner_user_id="runtime-owner", session_id="runtime-session", now_epoch=110,
    )
    assert lease_b is not None
    assert lease_b["fencing_token"] == 2
    assert not worker_a.transition(
        owner_user_id="runtime-owner", session_id="runtime-session", lease=lease_a,
        phase="PREFLIGHTING", updated_at="2026-10-19T00:00:02+00:00", now_epoch=111,
    )
    assert worker_b.transition(
        owner_user_id="runtime-owner", session_id="runtime-session", lease=lease_b,
        phase="PREFLIGHTING", updated_at="2026-10-19T00:00:03+00:00", now_epoch=111,
    )
    assert store.get_engine_session("runtime-session")["phase"] == "PREFLIGHTING"


def test_durable_runtime_rejects_foreign_owner_and_terminal_heartbeat(tmp_path):
    store = make_store(tmp_path)
    runtime = DurableEngineRuntime(store, worker_id="worker-incarnation-a")
    lease = runtime.create_session(
        owner_user_id="runtime-owner", session_id="runtime-session", mode="paper",
        now_iso="2026-10-19T00:00:00+00:00", now_epoch=100,
    )
    assert runtime.heartbeat(
        owner_user_id="runtime-other", session_id="runtime-session", now_epoch=101,
    ) is None
    assert not runtime.transition(
        owner_user_id="runtime-other", session_id="runtime-session", lease=lease,
        phase="STOPPED", updated_at="2026-10-19T00:00:02+00:00", now_epoch=102,
    )
    assert runtime.transition(
        owner_user_id="runtime-owner", session_id="runtime-session", lease=lease,
        phase="STOPPED", updated_at="2026-10-19T00:00:03+00:00", now_epoch=103,
    )
    assert runtime.heartbeat(
        owner_user_id="runtime-owner", session_id="runtime-session", now_epoch=104,
    ) is None
    assert store.release_engine_worker_lease(
        session_id="runtime-session", worker_id="worker-incarnation-a",
        fencing_token=lease["fencing_token"],
    ) is False
