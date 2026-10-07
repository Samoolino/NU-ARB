import asyncio
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.authenticated_feed_validation import (
    summarize_strict_book,
    validate_authenticated_feeds_until_resolved,
    validate_pair_format,
)


class RequestTimeout(Exception):
    pass


def run(coro):
    return asyncio.run(coro)


def test_pair_is_one_exact_uppercase_spot_symbol():
    assert validate_pair_format("BTC/USDT") == "BTC/USDT"
    for invalid in ("btc/usdt", "BTC-USDT", "BTC/USDT:USDT", "BTC/USDT,ETH/USDT", ""):
        with pytest.raises(ValueError):
            validate_pair_format(invalid)


def test_strict_book_requires_valid_sorted_levels_and_non_crossed_market():
    valid = summarize_strict_book({
        "bids": [[100, 1], [99, 2]],
        "asks": [[101, 1], [102, 2]],
        "timestamp": 123,
        "nonce": 9,
    })
    assert valid["ok"] is True
    assert valid["bestBid"] == 100
    assert valid["bestAsk"] == 101

    assert summarize_strict_book({
        "bids": [[101, 1]], "asks": [[100, 1]],
    })["reason"] == "crossed_or_locked_order_book"
    assert summarize_strict_book({
        "bids": [[99, 1], [100, 1]], "asks": [[101, 1]],
    })["reason"] == "bids_not_descending"
    assert summarize_strict_book({
        "bids": [[100, float("nan")]], "asks": [[101, 1]],
    })["reason"] == "bids_non_positive_or_non_finite_level"


def test_feed_validation_retries_transient_error_and_persists_same_pair_pass(tmp_path):
    calls = []
    updates = []

    async def probe(venue, symbol):
        calls.append((venue, symbol))
        if len(calls) == 1:
            error = RequestTimeout()
            error.validation_stage = "rest_orderbook"
            raise error
        return {
            "permissionValidated": True,
            "permissions": {
                "validationComplete": True,
                "liveEligible": False,
                "policyBlockers": ["ip_allowlist_not_proven"],
            },
            "market": {"symbol": symbol, "spot": True, "contract": False},
            "rest": {"ok": True, "bestBid": 100, "bestAsk": 101},
            "websocket": {"ok": True, "bestBid": 100, "bestAsk": 101},
            "restBookValid": True,
            "websocketBookValid": True,
            "blockers": [],
        }

    async def no_wait(_seconds):
        return None

    report = run(validate_authenticated_feeds_until_resolved(
        ("mexc",),
        "BTC/USDT",
        probe,
        tmp_path,
        base_retry_seconds=1,
        max_retry_seconds=2,
        sleep=no_wait,
        jitter=lambda low, _high: low,
        on_update=lambda venue, evidence: updates.append((venue, evidence["state"])),
    ))

    assert calls == [("mexc", "BTC/USDT"), ("mexc", "BTC/USDT")]
    assert report["runStatus"] == "RESOLVED"
    entry = report["venues"]["mexc"]
    assert entry["state"] == "VALIDATED"
    assert entry["attempts"] == 2
    assert entry["strictPairValidated"] is True
    assert entry["permissions"]["liveEligible"] is False
    assert entry["lastFailureType"] == "RequestTimeout"
    assert entry["failedStage"] == "rest_orderbook"
    assert report["ordersSubmitted"] is False
    assert report["balancesQueried"] is False
    assert report["transfersSubmitted"] is False
    assert ("mexc", "RETRYING_TRANSIENT_FAILURE") in updates
    assert json.loads(
        (tmp_path / "authenticated-feed-validation-latest.json").read_text()
    ) == report


def test_permission_failure_blocks_feed_validation_without_retry(tmp_path):
    calls = []

    async def probe(_venue, _symbol):
        calls.append(True)
        return {
            "permissionValidated": False,
            "permissions": {
                "validationComplete": False,
                "liveEligible": False,
                "policyBlockers": ["permission_evidence_unavailable"],
            },
            "blockers": ["permission_evidence_unavailable"],
        }

    report = run(validate_authenticated_feeds_until_resolved(
        ("htx",),
        "BTC/USDT",
        probe,
        tmp_path,
        base_retry_seconds=1,
        max_retry_seconds=1,
    ))

    assert len(calls) == 1
    assert report["venues"]["htx"]["state"] == "BLOCKED_VALIDATION"
    assert report["venues"]["htx"]["strictPairValidated"] is False
    assert report["venues"]["htx"]["restBookValid"] is False
