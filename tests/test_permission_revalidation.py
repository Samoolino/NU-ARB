import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.permission_revalidation import revalidate_permissions_until_resolved


class RequestTimeout(Exception):
    pass


class AuthenticationError(Exception):
    pass


def run(coro):
    return asyncio.run(coro)


def test_retries_transient_failures_and_persists_read_only_evidence(tmp_path):
    calls = []

    async def probe(venue):
        calls.append(venue)
        if len(calls) == 1:
            raise RequestTimeout("must not enter report")
        return {
            "validationStatus": "verified",
            "validationComplete": True,
            "liveEligible": False,
            "source": "venue key-scope endpoint",
            "policyBlockers": ["ip_allowlist_not_proven"],
            "tradePermission": "enabled",
        }

    async def no_wait(_seconds):
        return None

    report = run(revalidate_permissions_until_resolved(
        ("mexc",),
        probe,
        tmp_path,
        base_retry_seconds=1,
        max_retry_seconds=2,
        sleep=no_wait,
        jitter=lambda low, high: low,
    ))

    assert calls == ["mexc", "mexc"]
    assert report["runStatus"] == "RESOLVED"
    assert report["venues"]["mexc"]["state"] == "VALIDATED_POLICY_BLOCKED"
    assert report["venues"]["mexc"]["attempts"] == 2
    assert report["venues"]["mexc"]["policyBlockers"] == ["ip_allowlist_not_proven"]
    assert report["ordersSubmitted"] is False
    assert report["balancesQueried"] is False
    assert report["transfersSubmitted"] is False
    assert "must not enter report" not in json.dumps(report)
    persisted = json.loads((tmp_path / "permission-revalidation-latest.json").read_text())
    assert persisted == report


def test_terminal_authentication_error_is_not_retried(tmp_path):
    calls = []

    async def probe(_venue):
        calls.append(True)
        raise AuthenticationError("credentials must not enter report")

    report = run(revalidate_permissions_until_resolved(
        ("kucoin",),
        probe,
        tmp_path,
        base_retry_seconds=1,
        max_retry_seconds=1,
    ))

    assert len(calls) == 1
    entry = report["venues"]["kucoin"]
    assert entry["state"] == "BLOCKED_TERMINAL_ERROR"
    assert entry["lastFailureType"] == "AuthenticationError"
    assert entry["validationComplete"] is False
    assert "credentials must not enter report" not in json.dumps(report)


def test_unverified_endpoint_response_is_persisted_as_operator_blocker(tmp_path):
    async def probe(_venue):
        return {
            "validationStatus": "unverified",
            "validationComplete": False,
            "liveEligible": False,
            "policyBlockers": ["permission_evidence_unavailable"],
        }

    report = run(revalidate_permissions_until_resolved(
        ("htx",),
        probe,
        tmp_path,
        base_retry_seconds=1,
        max_retry_seconds=1,
    ))

    assert report["runStatus"] == "RESOLVED"
    assert report["venues"]["htx"]["state"] == "BLOCKED_UNVERIFIED_RESPONSE"
    assert report["venues"]["htx"]["attempts"] == 1
