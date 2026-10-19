from __future__ import annotations

import pytest

from arbx.engine_runtime import recovery_disposition


@pytest.mark.parametrize(
    ("phase", "checkpoint_verified", "expected"),
    [
        ("CREATED", False, "CLAIM_AND_START"),
        ("STARTING", False, "RESTART_PREFLIGHT"),
        ("PREFLIGHTING", False, "RESTART_PREFLIGHT"),
        ("RUNNING", True, "RESUME_CHECKPOINT"),
        ("RUNNING", False, "FAIL_UNSAFE_RECOVERY"),
        ("STOPPING", False, "COMPLETE_STOP"),
        ("STOPPED", False, "IGNORE_TERMINAL"),
        ("FAILED", False, "IGNORE_TERMINAL"),
        ("PREFLIGHT_FAILED", False, "IGNORE_TERMINAL"),
    ],
)
def test_recovery_disposition_is_conservative_and_phase_specific(
    phase, checkpoint_verified, expected
):
    assert recovery_disposition(
        {"phase": phase}, checkpoint_verified=checkpoint_verified
    ) == expected


def test_recovery_disposition_rejects_unknown_or_malformed_sessions():
    with pytest.raises(ValueError):
        recovery_disposition({"phase": "UNKNOWN"}, checkpoint_verified=False)
    with pytest.raises(ValueError):
        recovery_disposition({}, checkpoint_verified=False)
    with pytest.raises(ValueError):
        recovery_disposition({"phase": "RUNNING"}, checkpoint_verified="yes")
