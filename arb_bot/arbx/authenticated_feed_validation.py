"""Strict, read-only authenticated permission and REST/WebSocket feed checks."""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable


_RETRYABLE_ERRORS = {
    "DDoSProtection",
    "ExchangeNotAvailable",
    "NetworkError",
    "RateLimitExceeded",
    "RequestTimeout",
}
_TERMINAL_ERRORS = {
    "AuthenticationError",
    "BadRequest",
    "InvalidNonce",
    "NotSupported",
    "PermissionDenied",
}
_PAIR = re.compile(r"^[A-Z0-9]{2,24}/[A-Z0-9]{2,24}$")


def validate_pair_format(symbol: str) -> str:
    if not isinstance(symbol, str) or _PAIR.fullmatch(symbol) is None:
        raise ValueError("symbol must be one exact BASE/QUOTE pair, e.g. BTC/USDT")
    return symbol


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retryable(exc: Exception) -> bool:
    name = type(exc).__name__
    if name in _TERMINAL_ERRORS:
        return False
    return isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or name in _RETRYABLE_ERRORS


def _valid_levels(raw: object, side: str) -> tuple[bool, list[list[float]], str | None]:
    if not isinstance(raw, (list, tuple)) or not raw:
        return False, [], f"{side}_empty"
    levels: list[list[float]] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            return False, [], f"{side}_malformed_level"
        try:
            price, amount = float(row[0]), float(row[1])
        except (TypeError, ValueError, OverflowError):
            return False, [], f"{side}_non_numeric_level"
        if not math.isfinite(price) or not math.isfinite(amount) or price <= 0 or amount <= 0:
            return False, [], f"{side}_non_positive_or_non_finite_level"
        levels.append([price, amount])
    prices = [row[0] for row in levels]
    if side == "bids" and any(left < right for left, right in zip(prices, prices[1:])):
        return False, [], "bids_not_descending"
    if side == "asks" and any(left > right for left, right in zip(prices, prices[1:])):
        return False, [], "asks_not_ascending"
    return True, levels, None


def summarize_strict_book(book: object) -> dict:
    if not isinstance(book, dict):
        return {"ok": False, "reason": "order_book_not_an_object"}
    bids_ok, bids, bids_reason = _valid_levels(book.get("bids"), "bids")
    asks_ok, asks, asks_reason = _valid_levels(book.get("asks"), "asks")
    if not bids_ok or not asks_ok:
        return {"ok": False, "reason": bids_reason or asks_reason}
    if bids[0][0] >= asks[0][0]:
        return {"ok": False, "reason": "crossed_or_locked_order_book"}
    return {
        "ok": True,
        "bidLevels": len(bids),
        "askLevels": len(asks),
        "bestBid": bids[0][0],
        "bestAsk": asks[0][0],
        "spread": asks[0][0] - bids[0][0],
        "exchangeTimestampMs": book.get("timestamp"),
        "sequence": book.get("nonce"),
    }


def _safe_permissions(evidence: dict) -> dict:
    keys = (
        "validationStatus",
        "validationComplete",
        "liveEligible",
        "policyBlockers",
        "tradePermission",
        "spotAndMarginTradePermission",
        "withdrawalsDisabled",
        "internalTransfersDisabled",
        "universalTransfersDisabled",
        "ipRestricted",
    )
    return {key: evidence[key] for key in keys if key in evidence}


def _write_report(report: dict, report_dir: Path, stamp: str) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    for path in (
        report_dir / f"authenticated-feed-validation-{stamp}.json",
        report_dir / "authenticated-feed-validation-latest.json",
    ):
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
                json.dump(report, output, indent=2, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        if json.loads(path.read_text(encoding="utf-8")) != report:
            raise OSError(f"feed-validation report failed read-back validation: {path.name}")


async def validate_authenticated_feeds_until_resolved(
    venue_ids: tuple[str, ...],
    symbol: str,
    probe: Callable[[str, str], Awaitable[dict]],
    report_dir: Path,
    *,
    base_retry_seconds: float = 30.0,
    max_retry_seconds: float = 900.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[float, float], float] = random.uniform,
    on_update: Callable[[str, dict], None] | None = None,
) -> dict:
    """Retry only transient errors until strict same-pair REST and WS books pass."""
    symbol = validate_pair_format(symbol)
    if not venue_ids or len(set(venue_ids)) != len(venue_ids):
        raise ValueError("venue_ids must contain unique venues")
    if not callable(probe):
        raise ValueError("probe must be callable")
    if base_retry_seconds <= 0 or max_retry_seconds < base_retry_seconds:
        raise ValueError("retry intervals must be positive and max_retry_seconds >= base_retry_seconds")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    venues = {
        venue_id: {
            "state": "PENDING",
            "attempts": 0,
            "permissionValidated": False,
            "restBookValid": False,
            "websocketBookValid": False,
            "strictPairValidated": False,
            "blockers": [],
        }
        for venue_id in venue_ids
    }
    report = {
        "schemaVersion": 1,
        "startedAtUtc": _now(),
        "updatedAtUtc": _now(),
        "runStatus": "RUNNING",
        "scope": "authenticated_permission_and_read_only_same_pair_rest_websocket_books",
        "symbol": symbol,
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
            transient = set()
            for venue_id in venue_ids:
                if venue_id not in pending:
                    continue
                entry = venues[venue_id]
                entry["attempts"] += 1
                entry["lastAttemptAtUtc"] = _now()
                entry.pop("nextRetrySeconds", None)
                try:
                    result = await probe(venue_id, symbol)
                    if not isinstance(result, dict):
                        raise TypeError("feed validation returned an invalid result")
                except Exception as exc:
                    entry["lastFailureType"] = type(exc).__name__
                    failed_stage = getattr(exc, "validation_stage", None)
                    if isinstance(failed_stage, str):
                        entry["failedStage"] = failed_stage
                    entry["state"] = (
                        "RETRYING_TRANSIENT_FAILURE"
                        if _retryable(exc)
                        else "BLOCKED_TERMINAL_ERROR"
                    )
                    entry["blockers"] = (
                        ["transient_transport_failure"]
                        if _retryable(exc)
                        else ["operator_action_required"]
                    )
                    if _retryable(exc):
                        transient.add(venue_id)
                    else:
                        pending.remove(venue_id)
                    persist("RUNNING")
                    if on_update:
                        on_update(venue_id, dict(entry))
                    continue

                entry["permissionValidated"] = result.get("permissionValidated") is True
                if isinstance(result.get("permissions"), dict):
                    entry["permissions"] = _safe_permissions(result["permissions"])
                entry["market"] = result.get("market", {})
                entry["rest"] = result.get("rest", {})
                entry["websocket"] = result.get("websocket", {})
                entry["restBookValid"] = result.get("restBookValid") is True
                entry["websocketBookValid"] = result.get("websocketBookValid") is True
                entry["strictPairValidated"] = (
                    entry["permissionValidated"]
                    and entry["restBookValid"]
                    and entry["websocketBookValid"]
                )
                entry["blockers"] = list(result.get("blockers") or [])
                if entry["strictPairValidated"]:
                    entry["state"] = "VALIDATED"
                    pending.remove(venue_id)
                else:
                    entry["state"] = "BLOCKED_VALIDATION"
                    pending.remove(venue_id)
                    if not entry["blockers"]:
                        entry["blockers"] = ["strict_pair_validation_failed"]
                persist("RUNNING")
                if on_update:
                    on_update(venue_id, dict(entry))

            persist("RETRYING" if pending else "RESOLVED")
            if pending:
                retry_round += 1
                delay = min(max_retry_seconds, base_retry_seconds * (2 ** min(retry_round - 1, 20)))
                delay = min(max_retry_seconds, max(base_retry_seconds, jitter(delay * 0.8, delay * 1.2)))
                for venue_id in transient:
                    venues[venue_id]["nextRetrySeconds"] = round(delay, 2)
                    if on_update:
                        on_update(venue_id, dict(venues[venue_id]))
                persist("RETRYING")
                await sleep(delay)
    except asyncio.CancelledError:
        persist("INTERRUPTED")
        raise
    return report
