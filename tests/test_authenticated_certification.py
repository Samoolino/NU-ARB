import asyncio
import pathlib
import sys
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.authenticated_certification import (  # noqa: E402
    CertificationState,
    CertificationStatus,
    certify_authenticated_venues,
)
from arbx.venue_contract import (  # noqa: E402
    AuthenticationEvidence,
    Balance,
    Capability,
    CapabilityDeclaration,
    CapabilityReport,
    CapabilityStatus,
    ConnectionChannel,
    ConnectivityEvidence,
    EvidenceRecord,
    EvidenceStatus,
    Permission,
    PermissionAssessment,
    PermissionsEvidence,
)


SECRET = "TEST-CERTIFICATION-CREDENTIAL-NEVER-RETURN"


def declarations(*, unsupported=(), unverified=()):
    values = {
        capability: CapabilityDeclaration(CapabilityStatus.IMPLEMENTED)
        for capability in (
            Capability.BALANCES,
            Capability.PERMISSION_CHECK,
            Capability.PRIVATE_STREAM,
            Capability.ORDER_CREATE,
            Capability.ORDER_CANCEL,
        )
    }
    for capability in unsupported:
        values[capability] = CapabilityDeclaration(CapabilityStatus.UNSUPPORTED)
    for capability in unverified:
        values[capability] = CapabilityDeclaration(CapabilityStatus.UNVERIFIED)
    return CapabilityReport(declarations=values)


class FakeAdapter:
    def __init__(
        self,
        *,
        capability_report=None,
        auth_status=EvidenceStatus.VERIFIED,
        permission_status=EvidenceStatus.VERIFIED,
        stream_error=None,
        auth_error=None,
        balance_error=None,
    ):
        self.capabilities = capability_report or declarations()
        self.auth_status = auth_status
        self.permission_status = permission_status
        self.stream_error = stream_error
        self.auth_error = auth_error
        self.balance_error = balance_error
        self.calls = []
        self.create_order_calls = 0
        self.cancel_order_calls = 0

    async def connect(self):
        self.calls.append("connect")

    async def verify_authentication(self):
        self.calls.append("verify_authentication")
        if self.auth_error:
            raise RuntimeError(self.auth_error)
        return AuthenticationEvidence(EvidenceRecord(self.auth_status))

    async def get_balances(self):
        self.calls.append("get_balances")
        if self.balance_error:
            raise RuntimeError(self.balance_error)
        return (Balance("USD", free=Decimal("12.50")),)

    async def verify_permissions(self):
        self.calls.append("verify_permissions")
        return PermissionsEvidence(
            (PermissionAssessment(Permission.TRADE, EvidenceRecord(self.permission_status)),),
            checked_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
        )

    async def verify_private_stream(self):
        self.calls.append("verify_private_stream")
        if self.stream_error:
            raise RuntimeError(self.stream_error)
        return ConnectivityEvidence(
            ConnectionChannel.PRIVATE_STREAM,
            EvidenceStatus.VERIFIED,
            datetime(2026, 10, 7, tzinfo=timezone.utc),
        )

    async def create_order(self, *_args, **_kwargs):
        self.create_order_calls += 1
        raise AssertionError("certification must not place an order")

    async def cancel_order(self, *_args, **_kwargs):
        self.cancel_order_calls += 1
        raise AssertionError("certification must not cancel an order")

    async def close(self):
        self.calls.append("close")


def certify(credentials, factory):
    return asyncio.run(certify_authenticated_venues(credentials, adapter_factory=factory))


def state(report, key):
    return report.states[CertificationState[key].value]


def test_read_only_workflow_advances_observed_states_but_never_route_or_live():
    adapter = FakeAdapter()
    factory_calls = []

    def factory(venue_id, credentials):
        factory_calls.append((venue_id, credentials))
        return adapter

    result = certify({"venue-a": {"apiKey": SECRET, "secret": "test-secret"}}, factory)["venue-a"]

    assert factory_calls == [("venue-a", {"apiKey": SECRET, "secret": "test-secret"})]
    for name in (
        "AUTH_CONFIGURED",
        "AUTH_TRANSPORT_OK",
        "BALANCE_OK",
        "PERMISSIONS_OK",
        "PRIVATE_STREAM_OK",
        "EXECUTION_API_OK",
    ):
        assert state(result, name) == CertificationStatus.VERIFIED.value
    assert state(result, "EXECUTION_ROUTE_OK") == CertificationStatus.UNVERIFIED.value
    assert state(result, "LIVE_ELIGIBLE") == CertificationStatus.UNVERIFIED.value
    assert result.reason_codes["EXECUTION_ROUTE_OK"] == "external_certification_required"
    assert result.reason_codes["LIVE_ELIGIBLE"] == "external_certification_required"
    assert "get_balances" in adapter.calls
    assert adapter.calls[-1] == "close"
    assert adapter.create_order_calls == adapter.cancel_order_calls == 0
    assert SECRET not in repr(result)
    assert "test-secret" not in repr(result)


def test_missing_credentials_skip_factory_and_report_safe_reasons():
    called = []
    result = certify(
        {"empty": {}, "absent": None},
        lambda *args: called.append(args),
    )

    assert called == []
    for report in result.values():
        assert state(report, "AUTH_CONFIGURED") == CertificationStatus.FAILED.value
        assert report.reason_codes["AUTH_CONFIGURED"] == "credentials_missing"
        assert state(report, "AUTH_TRANSPORT_OK") == CertificationStatus.UNVERIFIED.value
        assert report.reason_codes["AUTH_TRANSPORT_OK"] == "authentication_required"
        assert state(report, "LIVE_ELIGIBLE") == CertificationStatus.UNVERIFIED.value


def test_permission_denial_does_not_become_permissions_ok_or_live_eligible():
    adapter = FakeAdapter(permission_status=EvidenceStatus.FAILED)
    report = certify({"venue": {"apiKey": "configured"}}, lambda *_: adapter)["venue"]

    assert state(report, "AUTH_TRANSPORT_OK") == CertificationStatus.VERIFIED.value
    assert state(report, "BALANCE_OK") == CertificationStatus.VERIFIED.value
    assert state(report, "PERMISSIONS_OK") == CertificationStatus.FAILED.value
    assert report.reason_codes["PERMISSIONS_OK"] == "trade_permission_denied"
    assert state(report, "LIVE_ELIGIBLE") != CertificationStatus.VERIFIED.value
    assert adapter.create_order_calls == adapter.cancel_order_calls == 0


def test_private_stream_failure_is_isolated_and_exception_message_is_not_returned():
    adapter = FakeAdapter(stream_error=f"private-key={SECRET}")
    report = certify({"venue": {"apiKey": "configured"}}, lambda *_: adapter)["venue"]

    assert state(report, "AUTH_TRANSPORT_OK") == CertificationStatus.VERIFIED.value
    assert state(report, "BALANCE_OK") == CertificationStatus.VERIFIED.value
    assert state(report, "PERMISSIONS_OK") == CertificationStatus.VERIFIED.value
    assert state(report, "PRIVATE_STREAM_OK") == CertificationStatus.FAILED.value
    assert report.reason_codes["PRIVATE_STREAM_OK"] == "private_stream_check_failed"
    assert SECRET not in repr(report)
    assert "private-key=" not in repr(report)
    assert adapter.create_order_calls == adapter.cancel_order_calls == 0


def test_unsupported_cancel_metadata_fails_execution_api_without_calling_orders():
    adapter = FakeAdapter(
        capability_report=declarations(unsupported=(Capability.ORDER_CANCEL,))
    )
    report = certify({"venue": {"apiKey": "configured"}}, lambda *_: adapter)["venue"]

    assert state(report, "EXECUTION_API_OK") == CertificationStatus.FAILED.value
    assert report.reason_codes["EXECUTION_API_OK"] == "order_cancel_unsupported"
    assert state(report, "EXECUTION_ROUTE_OK") == CertificationStatus.UNVERIFIED.value
    assert adapter.create_order_calls == adapter.cancel_order_calls == 0


def test_venue_failures_are_isolated_and_raw_adapter_errors_are_not_exposed():
    healthy = FakeAdapter()

    def factory(venue_id, _credentials):
        if venue_id == "broken":
            raise RuntimeError(f"apiKey={SECRET}")
        return healthy

    reports = certify(
        {"broken": {"apiKey": "configured"}, "healthy": {"apiKey": "configured"}},
        factory,
    )

    assert state(reports["broken"], "AUTH_TRANSPORT_OK") == CertificationStatus.FAILED.value
    assert reports["broken"].reason_codes["AUTH_TRANSPORT_OK"] == "adapter_initialization_failed"
    assert state(reports["healthy"], "AUTH_TRANSPORT_OK") == CertificationStatus.VERIFIED.value
    assert SECRET not in repr(reports)
    assert healthy.create_order_calls == healthy.cancel_order_calls == 0
