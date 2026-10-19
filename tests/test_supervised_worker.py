from __future__ import annotations

from arbx.supervised_worker import SupervisedPaperWorker


class FakeRuntime:
    def __init__(self, lease):
        self.lease = lease
        self.heartbeats = 0

    def heartbeat(self, **kwargs):
        self.heartbeats += 1
        return self.lease


class FakeStore:
    def __init__(self, session):
        self.session = session

    def get_engine_session(self, session_id):
        return self.session


def test_worker_does_not_execute_or_persist_when_lease_is_lost():
    runtime = FakeRuntime(None)
    worker = SupervisedPaperWorker(runtime=runtime, store=FakeStore({"phase": "RUNNING", "owner_user_id": "u1"}))
    effects = []

    result = worker.run_once(
        owner_user_id="u1",
        session_id="s1",
        now_epoch=100,
        checkpoint_verified=lambda session: True,
        execute_step=lambda session, lease: effects.append("execute"),
        persist_checkpoint_fenced=lambda session, lease: effects.append("checkpoint"),
    )

    assert result == "LEASE_LOST"
    assert effects == []


def test_worker_passes_current_lease_to_each_persistence_boundary():
    lease = {"session_id": "s1", "worker_id": "w1", "fencing_token": 7}
    runtime = FakeRuntime(lease)
    worker = SupervisedPaperWorker(runtime=runtime, store=FakeStore({"phase": "RUNNING", "owner_user_id": "u1"}))
    effects = []

    result = worker.run_once(
        owner_user_id="u1",
        session_id="s1",
        now_epoch=100,
        checkpoint_verified=lambda session: True,
        execute_step=lambda session, fence: effects.append(("execute", fence["fencing_token"])),
        persist_checkpoint_fenced=lambda session, fence: effects.append(("checkpoint", fence["fencing_token"])),
    )

    assert result == "STEP_COMPLETED"
    assert effects == [("execute", 7), ("checkpoint", 7)]


def test_worker_fails_closed_on_owner_mismatch_before_heartbeat():
    runtime = FakeRuntime({"session_id": "s1", "worker_id": "w1", "fencing_token": 7})
    worker = SupervisedPaperWorker(runtime=runtime, store=FakeStore({"phase": "RUNNING", "owner_user_id": "other"}))

    result = worker.run_once(
        owner_user_id="u1",
        session_id="s1",
        now_epoch=100,
        checkpoint_verified=lambda session: True,
        execute_step=lambda session, lease: None,
        persist_checkpoint_fenced=lambda session, lease: None,
    )

    assert result == "NOT_FOUND"
    assert runtime.heartbeats == 0
