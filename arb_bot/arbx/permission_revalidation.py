"""Persistent, read-only retries for transient authenticated permission probes."""

from __future__ import annotations

import asyncio
import json
import os
import random
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable


PermissionProbe = Callable[[str], Awaitable[dict]]
StatusCallback = Callable[[str, dict], None]

_RETRYABLE_ERRORS = {
    "DDoSProtection",
    "ExchangeNotAvailable",
    "NetworkError",
    "RateLimitExceeded",
    "RequestTimeout",
}
_TERMINAL_ERRORS = {
    "AuthenticationError",
    "ArgumentsRequired",
    "BadRequest",
    "InvalidNonce",
    "NotSupported",
    "PermissionDenied",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_class(exc: Exception) -> str:
    return type(exc).__name__


def _is_retryable(exc: Exception) -> bool:
    name = _error_class(exc)
    if name in _TERMINAL_ERRORS:
        return False
    return isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or name in _RETRYABLE_ERRORS


def _safe_evidence(result: dict) -> dict:
    fields = (
        "validationStatus",
        "validationComplete",
        "liveEligible",
        "source",
        "policyBlockers",
        "tradePermission",
        "spotAndMarginTradePermission",
        "withdrawalsDisabled",
        "internalTransfersDisabled",
        "universalTransfersDisabled",
        "ipRestricted",
    )
    return {key: result[key] for key in fields if key in result}


def _write_report(report: dict, report_dir: Path, stamp: str) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    paths = (
        report_dir / f"permission-revalidation-{stamp}.json",
        report_dir / "permission-revalidation-latest.json",
    )
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    for path in paths:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=report_dir,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        persisted = json.loads(path.read_text(encoding="utf-8"))
        if persisted != report:
            raise OSError(f"permission revalidation report failed read-back validation: {path.name}")


async def revalidate_permissions_until_resolved(
    venue_ids: tuple[str, ...],
    probe: PermissionProbe,
    report_dir: Path,
    *,
    base_retry_seconds: float = 30.0,
    max_retry_seconds: float = 900.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[float, float], float] = random.uniform,
    on_update: StatusCallback | None = None,
) -> dict:
    """Retry transient failures until each probe validates or has a terminal blocker.

    This performs no order, balance, or transfer operation. A structurally valid
    permission response resolves the retry even when policy evidence blocks live
    eligibility. Authentication, unsupported-endpoint, and incomplete-response
    outcomes are terminal and are persisted for operator action.
    """
    if not venue_ids or len(set(venue_ids)) != len(venue_ids):
        raise ValueError("venue_ids must contain unique venues")
    if not callable(probe):
        raise ValueError("probe must be callable")
    if base_retry_seconds <= 0 or max_retry_seconds < base_retry_seconds:
        raise ValueError("retry intervals must be positive and max_retry_seconds >= base_retry_seconds")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started_at = _now()
    venues = {
        venue_id: {
            "state": "PENDING",
            "attempts": 0,
            "validationComplete": False,
            "liveEligible": False,
            "policyBlockers": [],
        }
        for venue_id in venue_ids
    }
    report = {
        "schemaVersion": 1,
        "startedAtUtc": started_at,
        "updatedAtUtc": started_at,
        "runStatus": "RUNNING",
        "scope": "read_only_authenticated_permission_probes",
        "ordersSubmitted": False,
        "balancesQueried": False,
        "transfersSubmitted": False,
        "retryPolicy": {
            "initialSeconds": base_retry_seconds,
            "maximumSeconds": max_retry_seconds,
            "transientErrorsOnly": True,
        },
        "venues": venues,
    }

    def persist(status: str) -> None:
        report["updatedAtUtc"] = _now()
        report["runStatus"] = status
        _write_report(report, report_dir, stamp)

    pending = set(venue_ids)
    retry_round = 0
    persist("RUNNING")
    try:
        while pending:
            retryable_pending = set()
            for venue_id in venue_ids:
                if venue_id not in pending:
                    continue
                entry = venues[venue_id]
                entry["attempts"] += 1
                entry["lastAttemptAtUtc"] = _now()
                entry.pop("nextRetrySeconds", None)
                try:
                    result = await probe(venue_id)
                    if not isinstance(result, dict):
                        raise TypeError("permission probe returned an invalid result")
                except Exception as exc:
                    error_type = _error_class(exc)
                    entry["lastFailureType"] = error_type
                    if _is_retryable(exc):
                        entry["state"] = "RETRYING_TRANSIENT_FAILURE"
                        retryable_pending.add(venue_id)
                    else:
                        entry["state"] = "BLOCKED_TERMINAL_ERROR"
                        entry["policyBlockers"] = ["operator_action_required"]
                        pending.remove(venue_id)
                    persist("RUNNING")
                    if on_update is not None:
                        on_update(venue_id, dict(entry))
                    continue

                entry.update(_safe_evidence(result))
                if result.get("validationComplete") is True:
                    entry["state"] = (
                        "VALIDATED"
                        if result.get("liveEligible") is True
                        else "VALIDATED_POLICY_BLOCKED"
                    )
                    pending.remove(venue_id)
                else:
                    entry["state"] = "BLOCKED_UNVERIFIED_RESPONSE"
                    if not entry.get("policyBlockers"):
                        entry["policyBlockers"] = ["permission_evidence_unverified"]
                    pending.remove(venue_id)
                persist("RUNNING")
                if on_update is not None:
                    on_update(venue_id, dict(entry))

            persist("RETRYING" if pending else "RESOLVED")
            if pending:
                retry_round += 1
                delay = min(
                    max_retry_seconds,
                    base_retry_seconds * (2 ** min(retry_round - 1, 20)),
                )
                delay = min(
                    max_retry_seconds,
                    max(base_retry_seconds, jitter(delay * 0.8, delay * 1.2)),
                )
                for venue_id in retryable_pending:
                    venues[venue_id]["nextRetrySeconds"] = round(delay, 2)
                    if on_update is not None:
                        on_update(venue_id, dict(venues[venue_id]))
                persist("RETRYING")
                await sleep(delay)
    except asyncio.CancelledError:
        persist("INTERRUPTED")
        raise

    return report
