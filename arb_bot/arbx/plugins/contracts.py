"""Typed contracts for optional, read-only external evidence providers.

Provider adapters must not receive exchange credentials or expose trading, signing,
withdrawal, or transaction-submission capabilities. Responses are untrusted and
must carry source timestamps and explicit status.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping, Protocol


class EvidenceStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    INVALID = "invalid"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    request_id: str
    operation: str
    parameters: Mapping[str, str]
    timeout_seconds: float = 3.0


@dataclass(frozen=True, slots=True)
class ProviderEvidence:
    provider: str
    operation: str
    status: EvidenceStatus
    observed_at: datetime
    retrieved_at: datetime
    latency_ms: float
    payload: Mapping[str, Any] | None = None
    payload_sha256: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.operation.strip():
            raise ValueError("provider and operation are required")
        if self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        if self.observed_at.tzinfo is None or self.retrieved_at.tzinfo is None:
            raise ValueError("evidence timestamps must be timezone-aware")
        if self.status is EvidenceStatus.OK and self.payload is None:
            raise ValueError("successful evidence requires a payload")
        if self.status is not EvidenceStatus.OK and self.payload is not None:
            raise ValueError("non-success evidence must not carry a trusted payload")

    def is_fresh(self, *, now: datetime | None = None, max_age_seconds: float = 60.0) -> bool:
        if self.status is not EvidenceStatus.OK or max_age_seconds < 0:
            return False
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        age = (current - self.observed_at).total_seconds()
        return 0 <= age <= max_age_seconds


class ReadOnlyEvidenceProvider(Protocol):
    """Small provider surface: fetch evidence only, never perform external actions."""

    name: str

    async def fetch(self, request: ProviderRequest) -> ProviderEvidence:
        """Fetch validated, timestamped evidence within the request timeout."""
        ...
