from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import pytest
from arbx.plugins.contracts import EvidenceStatus, ProviderEvidence, ProviderRequest
from arbx.plugins.runtime import PluginPermission, PluginPermissionError, PluginRunner

class FakeProvider:
    name = "fixture"
    def __init__(self, delay=0, failure=None): self.delay, self.failure = delay, failure
    async def fetch(self, request):
        if self.delay: await asyncio.sleep(self.delay)
        if self.failure: raise self.failure
        now = datetime.now(timezone.utc)
        return ProviderEvidence(provider=self.name, operation=request.operation, status=EvidenceStatus.OK,
            observed_at=now, retrieved_at=now, latency_ms=1, payload={"value": 42})

def request(timeout=1):
    return ProviderRequest(request_id="req-1", operation="lookup", parameters={}, timeout_seconds=timeout)

@pytest.mark.asyncio
async def test_allowed_provider_is_audited_with_payload_hash():
    audit = []
    async def writer(record): audit.append(record)
    runner = PluginRunner((PluginPermission("fixture", frozenset({"lookup"})),), writer)
    result = await runner.invoke(FakeProvider(), request())
    assert result.status is EvidenceStatus.OK
    assert audit[0]["outcome"] == "completed" and audit[0]["payload_sha256"]

@pytest.mark.asyncio
async def test_unpermitted_operation_is_denied_and_audited():
    audit = []
    async def writer(record): audit.append(record)
    runner = PluginRunner((), writer)
    with pytest.raises(PluginPermissionError): await runner.invoke(FakeProvider(), request())
    assert audit[0]["outcome"] == "denied"

@pytest.mark.asyncio
async def test_timeout_is_bounded_and_reported():
    audit = []
    async def writer(record): audit.append(record)
    runner = PluginRunner((PluginPermission("fixture", frozenset({"lookup"})),), writer, timeout_seconds=0.01)
    result = await runner.invoke(FakeProvider(delay=0.1), request())
    assert result.status is EvidenceStatus.ERROR and result.error_code == "timeout"
    assert audit[0]["outcome"] == "timeout"

@pytest.mark.asyncio
async def test_provider_exception_is_sanitized_and_audited():
    audit = []
    async def writer(record): audit.append(record)
    runner = PluginRunner((PluginPermission("fixture", frozenset({"lookup"})),), writer)
    result = await runner.invoke(FakeProvider(failure=RuntimeError("private details")), request())
    assert result.status is EvidenceStatus.ERROR and result.error_code == "RuntimeError"
    assert "private details" not in repr(audit)
