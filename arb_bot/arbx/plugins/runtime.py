"""Permission-scoped, bounded runner for optional read-only evidence providers."""
from __future__ import annotations
import asyncio, hashlib, json, time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable
from .contracts import EvidenceStatus, ProviderEvidence, ProviderRequest, ReadOnlyEvidenceProvider

@dataclass(frozen=True, slots=True)
class PluginPermission:
    provider: str
    operations: frozenset[str]
    scope: str = "evidence:read"

class PluginPermissionError(PermissionError): pass
AuditWriter = Callable[[dict], Awaitable[None]]

class PluginRunner:
    """Only invokes explicitly allowed provider/operation pairs; no action capabilities."""
    def __init__(self, permissions: tuple[PluginPermission, ...], audit_writer: AuditWriter, *, timeout_seconds: float = 2.0, max_timeout_seconds: float = 10.0):
        if timeout_seconds <= 0 or max_timeout_seconds <= 0 or timeout_seconds > max_timeout_seconds:
            raise ValueError("timeout must be positive and bounded")
        self._permissions = {(p.provider, op): p.scope for p in permissions for op in p.operations}
        self._audit_writer = audit_writer
        self._timeout_seconds = timeout_seconds

    async def invoke(self, provider: ReadOnlyEvidenceProvider, request: ProviderRequest) -> ProviderEvidence:
        started = time.monotonic()
        provider_name = getattr(provider, "name", "")
        scope = self._permissions.get((provider_name, request.operation))
        if scope != "evidence:read":
            await self._audit("denied", provider_name, request, scope, 0, "permission_denied", None)
            raise PluginPermissionError("provider operation is not permitted")
        timeout = min(self._timeout_seconds, request.timeout_seconds)
        if timeout <= 0:
            raise ValueError("request timeout must be positive")
        try:
            evidence = await asyncio.wait_for(provider.fetch(request), timeout=timeout)
            if evidence.provider != provider_name or evidence.operation != request.operation:
                raise ValueError("provider identity or operation mismatch")
            payload_hash = evidence.payload_sha256
            if evidence.payload is not None and not payload_hash:
                raw = json.dumps(dict(evidence.payload), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                payload_hash = hashlib.sha256(raw).hexdigest()
            await self._audit("completed", provider_name, request, scope, int((time.monotonic()-started)*1000), evidence.status.value, payload_hash)
            return evidence
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic()-started)*1000)
            await self._audit("timeout", provider_name, request, scope, elapsed, "timeout", None)
            now = datetime.now(timezone.utc)
            return ProviderEvidence(provider=provider_name, operation=request.operation, status=EvidenceStatus.ERROR,
                observed_at=now, retrieved_at=now, latency_ms=elapsed, error_code="timeout")
        except Exception as exc:
            elapsed = int((time.monotonic()-started)*1000)
            await self._audit("failed", provider_name, request, scope, elapsed, type(exc).__name__, None)
            now = datetime.now(timezone.utc)
            return ProviderEvidence(provider=provider_name, operation=request.operation, status=EvidenceStatus.ERROR,
                observed_at=now, retrieved_at=now, latency_ms=elapsed, error_code=type(exc).__name__)

    async def _audit(self, outcome, provider, request, scope, latency_ms, result_code, payload_hash):
        await self._audit_writer({"provider": provider, "operation": request.operation, "request_id": request.request_id,
            "scope": scope, "outcome": outcome, "latency_ms": latency_ms, "result_code": result_code,
            "payload_sha256": payload_hash, "recorded_at": datetime.now(timezone.utc).isoformat()})
