from datetime import datetime, timedelta, timezone

import pytest

from arbx.plugins.contracts import EvidenceStatus, ProviderEvidence


def evidence(**overrides):
    now = datetime.now(timezone.utc)
    values = dict(
        provider="fake-provider",
        operation="token_metadata",
        status=EvidenceStatus.OK,
        observed_at=now,
        retrieved_at=now,
        latency_ms=12.0,
        payload={"symbol": "ABC"},
    )
    values.update(overrides)
    return ProviderEvidence(**values)


def test_success_evidence_is_fresh_within_age_limit():
    now = datetime.now(timezone.utc)
    item = evidence(observed_at=now - timedelta(seconds=5), retrieved_at=now)
    assert item.is_fresh(now=now, max_age_seconds=10)


def test_old_evidence_is_not_fresh():
    now = datetime.now(timezone.utc)
    item = evidence(observed_at=now - timedelta(minutes=5), retrieved_at=now)
    assert not item.is_fresh(now=now, max_age_seconds=30)


def test_unavailable_evidence_never_counts_as_fresh():
    now = datetime.now(timezone.utc)
    item = evidence(status=EvidenceStatus.UNAVAILABLE, payload=None)
    assert not item.is_fresh(now=now)


def test_naive_timestamps_are_rejected():
    now = datetime.now()
    with pytest.raises(ValueError, match="timezone-aware"):
        evidence(observed_at=now, retrieved_at=now)


def test_negative_latency_is_rejected():
    with pytest.raises(ValueError, match="cannot be negative"):
        evidence(latency_ms=-1)


def test_non_success_evidence_cannot_carry_payload():
    with pytest.raises(ValueError, match="must not carry"):
        evidence(status=EvidenceStatus.ERROR)
