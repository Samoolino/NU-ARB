"""Read-only authenticated certification for explicitly configured venues.

The workflow uses a caller-supplied adapter factory and only performs
authentication, balance, permission, and private-stream checks. Order-create
and order-cancel are read as static capability declarations; the corresponding
adapter methods are never invoked. Route and live eligibility remain
unverified until a separate external certification process provides evidence.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Callable, Mapping

from .venue_contract import (
    AuthenticationEvidence,
    Balance,
    Capability,
    CapabilityReport,
    CapabilityStatus,
    ConnectivityEvidence,
    ConnectionChannel,
    EvidenceStatus,
    Permission,
    PermissionsEvidence,
    VenueAdapter,
)


class CertificationState(str, Enum):
    AUTH_CONFIGURED = "AUTH_CONFIGURED"
    AUTH_TRANSPORT_OK = "AUTH_TRANSPORT_OK"
    BALANCE_OK = "BALANCE_OK"
    PERMISSIONS_OK = "PERMISSIONS_OK"
    PRIVATE_STREAM_OK = "PRIVATE_STREAM_OK"
    EXECUTION_API_OK = "EXECUTION_API_OK"
    EXECUTION_ROUTE_OK = "EXECUTION_ROUTE_OK"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"


class CertificationStatus(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    UNVERIFIED = "unverified"


class SafeReason(str, Enum):
    CREDENTIALS_MISSING = "credentials_missing"
    CREDENTIALS_INVALID = "credentials_invalid"
    ADAPTER_INITIALIZATION_FAILED = "adapter_initialization_failed"
    CONNECT_FAILED = "connect_failed"
    AUTH_CHECK_FAILED = "authentication_check_failed"
    AUTH_REJECTED = "authentication_rejected"
    AUTH_UNVERIFIED = "authentication_unverified"
    AUTHENTICATION_REQUIRED = "authentication_required"
    CAPABILITY_METADATA_UNAVAILABLE = "capability_metadata_unavailable"
    BALANCE_CAPABILITY_UNSUPPORTED = "balance_capability_unsupported"
    BALANCE_CAPABILITY_UNVERIFIED = "balance_capability_unverified"
    BALANCE_CHECK_FAILED = "balance_check_failed"
    BALANCE_RESPONSE_INVALID = "balance_response_invalid"
    PERMISSION_CAPABILITY_UNSUPPORTED = "permission_capability_unsupported"
    PERMISSION_CAPABILITY_UNVERIFIED = "permission_capability_unverified"
    PERMISSION_CHECK_FAILED = "permission_check_failed"
    TRADE_PERMISSION_DENIED = "trade_permission_denied"
    TRADE_PERMISSION_UNVERIFIED = "trade_permission_unverified"
    PRIVATE_STREAM_UNSUPPORTED = "private_stream_unsupported"
    PRIVATE_STREAM_UNVERIFIED = "private_stream_unverified"
    PRIVATE_STREAM_CHECK_FAILED = "private_stream_check_failed"
    ORDER_CREATE_UNSUPPORTED = "order_create_unsupported"
    ORDER_CANCEL_UNSUPPORTED = "order_cancel_unsupported"
    EXECUTION_CAPABILITY_UNVERIFIED = "execution_capability_unverified"
    EXTERNAL_CERTIFICATION_REQUIRED = "external_certification_required"


AdapterFactory = Callable[[str, Mapping[str, str]], VenueAdapter]


@dataclass(frozen=True, slots=True)
class VenueAuthenticatedCertification:
    """Safe state and reason-code report; never contains credential data."""

    venue_id: str
    states: Mapping[str, str]
    reason_codes: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "states", MappingProxyType(dict(self.states)))
        object.__setattr__(self, "reason_codes", MappingProxyType(dict(self.reason_codes)))

    def to_dict(self) -> dict:
        return {
            "venueId": self.venue_id,
            "states": dict(self.states),
            "reasonCodes": dict(self.reason_codes),
        }


def _valid_credentials(credentials: object) -> bool:
    return (
        isinstance(credentials, Mapping)
        and bool(credentials)
        and all(
            isinstance(key, str)
            and bool(key.strip())
            and isinstance(value, str)
            and bool(value.strip())
            for key, value in credentials.items()
        )
    )


def _credential_reason(credentials: object) -> SafeReason:
    if not isinstance(credentials, Mapping) or not credentials:
        return SafeReason.CREDENTIALS_MISSING
    return SafeReason.CREDENTIALS_INVALID


def _set(
    states: dict[str, str],
    reasons: dict[str, str],
    state: CertificationState,
    status: CertificationStatus,
    reason: SafeReason | None = None,
) -> None:
    states[state.value] = status.value
    if reason is None:
        reasons.pop(state.value, None)
    else:
        reasons[state.value] = reason.value


def _capability_status(adapter: VenueAdapter, capability: Capability) -> CapabilityStatus:
    report = adapter.capabilities
    if not isinstance(report, CapabilityReport):
        return CapabilityStatus.UNVERIFIED
    return report.declaration_for(capability).status


async def _within(awaitable, timeout_seconds: float):
    return await asyncio.wait_for(awaitable, timeout=timeout_seconds)


async def _certify_one(
    venue_id: str,
    credentials: object,
    adapter_factory: AdapterFactory,
    timeout_seconds: float,
) -> VenueAuthenticatedCertification:
    states = {state.value: CertificationStatus.UNVERIFIED.value for state in CertificationState}
    reasons: dict[str, str] = {}
    adapter = None

    if not _valid_credentials(credentials):
        credential_reason = _credential_reason(credentials)
        _set(states, reasons, CertificationState.AUTH_CONFIGURED,
             CertificationStatus.FAILED, credential_reason)
        _set(states, reasons, CertificationState.AUTH_TRANSPORT_OK,
             CertificationStatus.UNVERIFIED, SafeReason.AUTHENTICATION_REQUIRED)
        for state in (
            CertificationState.BALANCE_OK,
            CertificationState.PERMISSIONS_OK,
            CertificationState.PRIVATE_STREAM_OK,
        ):
            _set(states, reasons, state, CertificationStatus.UNVERIFIED,
                 SafeReason.AUTHENTICATION_REQUIRED)
        _set(states, reasons, CertificationState.EXECUTION_API_OK,
             CertificationStatus.UNVERIFIED, SafeReason.CAPABILITY_METADATA_UNAVAILABLE)
    else:
        _set(states, reasons, CertificationState.AUTH_CONFIGURED, CertificationStatus.VERIFIED)
        try:
            # A private copy prevents a factory from mutating caller-owned saved credentials.
            adapter = adapter_factory(venue_id, dict(credentials))
        except Exception:
            _set(states, reasons, CertificationState.AUTH_TRANSPORT_OK,
                 CertificationStatus.FAILED, SafeReason.ADAPTER_INITIALIZATION_FAILED)
        if adapter is None and states[CertificationState.AUTH_TRANSPORT_OK.value] == CertificationStatus.UNVERIFIED.value:
            _set(states, reasons, CertificationState.AUTH_TRANSPORT_OK,
                 CertificationStatus.FAILED, SafeReason.ADAPTER_INITIALIZATION_FAILED)

        authenticated = False
        if adapter is not None:
            try:
                await _within(adapter.connect(), timeout_seconds)
            except Exception:
                _set(states, reasons, CertificationState.AUTH_TRANSPORT_OK,
                     CertificationStatus.FAILED, SafeReason.CONNECT_FAILED)
            else:
                try:
                    result = await _within(adapter.verify_authentication(), timeout_seconds)
                    if isinstance(result, AuthenticationEvidence) and result.evidence.status is EvidenceStatus.VERIFIED:
                        authenticated = True
                        _set(states, reasons, CertificationState.AUTH_TRANSPORT_OK,
                             CertificationStatus.VERIFIED)
                    elif isinstance(result, AuthenticationEvidence) and result.evidence.status is EvidenceStatus.FAILED:
                        _set(states, reasons, CertificationState.AUTH_TRANSPORT_OK,
                             CertificationStatus.FAILED, SafeReason.AUTH_REJECTED)
                    else:
                        _set(states, reasons, CertificationState.AUTH_TRANSPORT_OK,
                             CertificationStatus.UNVERIFIED, SafeReason.AUTH_UNVERIFIED)
                except Exception:
                    _set(states, reasons, CertificationState.AUTH_TRANSPORT_OK,
                         CertificationStatus.FAILED, SafeReason.AUTH_CHECK_FAILED)

            # Static capability declarations are inspected only; no order API is called.
            try:
                create_status = _capability_status(adapter, Capability.ORDER_CREATE)
                cancel_status = _capability_status(adapter, Capability.ORDER_CANCEL)
            except Exception:
                create_status = cancel_status = CapabilityStatus.UNVERIFIED
                execution_reason = SafeReason.CAPABILITY_METADATA_UNAVAILABLE
                execution_status = CertificationStatus.UNVERIFIED
            else:
                unsupported = (
                    (Capability.ORDER_CREATE, create_status, SafeReason.ORDER_CREATE_UNSUPPORTED),
                    (Capability.ORDER_CANCEL, cancel_status, SafeReason.ORDER_CANCEL_UNSUPPORTED),
                )
                unavailable = next(
                    (reason for _, status, reason in unsupported if status is CapabilityStatus.UNSUPPORTED),
                    None,
                )
                if unavailable is not None:
                    execution_status = CertificationStatus.FAILED
                    execution_reason = unavailable
                elif (
                    create_status is CapabilityStatus.IMPLEMENTED
                    and cancel_status is CapabilityStatus.IMPLEMENTED
                ):
                    execution_status = CertificationStatus.VERIFIED
                    execution_reason = None
                else:
                    execution_status = CertificationStatus.UNVERIFIED
                    execution_reason = SafeReason.EXECUTION_CAPABILITY_UNVERIFIED
            _set(states, reasons, CertificationState.EXECUTION_API_OK,
                 execution_status, execution_reason)

            if authenticated:
                try:
                    balance_capability = _capability_status(adapter, Capability.BALANCES)
                    if balance_capability is CapabilityStatus.UNSUPPORTED:
                        _set(states, reasons, CertificationState.BALANCE_OK,
                             CertificationStatus.FAILED, SafeReason.BALANCE_CAPABILITY_UNSUPPORTED)
                    elif balance_capability is not CapabilityStatus.IMPLEMENTED:
                        _set(states, reasons, CertificationState.BALANCE_OK,
                             CertificationStatus.UNVERIFIED, SafeReason.BALANCE_CAPABILITY_UNVERIFIED)
                    else:
                        balances = await _within(adapter.get_balances(), timeout_seconds)
                        if isinstance(balances, tuple) and all(isinstance(item, Balance) for item in balances):
                            _set(states, reasons, CertificationState.BALANCE_OK, CertificationStatus.VERIFIED)
                        else:
                            _set(states, reasons, CertificationState.BALANCE_OK,
                                 CertificationStatus.FAILED, SafeReason.BALANCE_RESPONSE_INVALID)
                except Exception:
                    _set(states, reasons, CertificationState.BALANCE_OK,
                         CertificationStatus.FAILED, SafeReason.BALANCE_CHECK_FAILED)

                try:
                    permission_capability = _capability_status(adapter, Capability.PERMISSION_CHECK)
                    if permission_capability is CapabilityStatus.UNSUPPORTED:
                        _set(states, reasons, CertificationState.PERMISSIONS_OK,
                             CertificationStatus.FAILED, SafeReason.PERMISSION_CAPABILITY_UNSUPPORTED)
                    elif permission_capability is not CapabilityStatus.IMPLEMENTED:
                        _set(states, reasons, CertificationState.PERMISSIONS_OK,
                             CertificationStatus.UNVERIFIED, SafeReason.PERMISSION_CAPABILITY_UNVERIFIED)
                    else:
                        permission_result = await _within(adapter.verify_permissions(), timeout_seconds)
                        trade_assessments = (
                            tuple(item for item in permission_result.assessments
                                  if item.permission is Permission.TRADE)
                            if isinstance(permission_result, PermissionsEvidence)
                            else ()
                        )
                        if any(item.evidence.status is EvidenceStatus.FAILED for item in trade_assessments):
                            _set(states, reasons, CertificationState.PERMISSIONS_OK,
                                 CertificationStatus.FAILED, SafeReason.TRADE_PERMISSION_DENIED)
                        elif trade_assessments and all(
                            item.evidence.status is EvidenceStatus.VERIFIED for item in trade_assessments
                        ):
                            _set(states, reasons, CertificationState.PERMISSIONS_OK,
                                 CertificationStatus.VERIFIED)
                        else:
                            _set(states, reasons, CertificationState.PERMISSIONS_OK,
                                 CertificationStatus.UNVERIFIED, SafeReason.TRADE_PERMISSION_UNVERIFIED)
                except Exception:
                    _set(states, reasons, CertificationState.PERMISSIONS_OK,
                         CertificationStatus.FAILED, SafeReason.PERMISSION_CHECK_FAILED)

                try:
                    private_capability = _capability_status(adapter, Capability.PRIVATE_STREAM)
                    if private_capability is CapabilityStatus.UNSUPPORTED:
                        _set(states, reasons, CertificationState.PRIVATE_STREAM_OK,
                             CertificationStatus.FAILED, SafeReason.PRIVATE_STREAM_UNSUPPORTED)
                    elif private_capability is not CapabilityStatus.IMPLEMENTED:
                        _set(states, reasons, CertificationState.PRIVATE_STREAM_OK,
                             CertificationStatus.UNVERIFIED, SafeReason.PRIVATE_STREAM_UNVERIFIED)
                    else:
                        stream_result = await _within(adapter.verify_private_stream(), timeout_seconds)
                        if (
                            isinstance(stream_result, ConnectivityEvidence)
                            and stream_result.channel is ConnectionChannel.PRIVATE_STREAM
                            and stream_result.status is EvidenceStatus.VERIFIED
                        ):
                            _set(states, reasons, CertificationState.PRIVATE_STREAM_OK,
                                 CertificationStatus.VERIFIED)
                        else:
                            _set(states, reasons, CertificationState.PRIVATE_STREAM_OK,
                                 CertificationStatus.UNVERIFIED, SafeReason.PRIVATE_STREAM_UNVERIFIED)
                except Exception:
                    _set(states, reasons, CertificationState.PRIVATE_STREAM_OK,
                         CertificationStatus.FAILED, SafeReason.PRIVATE_STREAM_CHECK_FAILED)
            else:
                for state in (
                    CertificationState.BALANCE_OK,
                    CertificationState.PERMISSIONS_OK,
                    CertificationState.PRIVATE_STREAM_OK,
                ):
                    _set(states, reasons, state, CertificationStatus.UNVERIFIED,
                         SafeReason.AUTHENTICATION_REQUIRED)

        if adapter is None:
            for state in (
                CertificationState.BALANCE_OK,
                CertificationState.PERMISSIONS_OK,
                CertificationState.PRIVATE_STREAM_OK,
            ):
                _set(states, reasons, state, CertificationStatus.UNVERIFIED,
                     SafeReason.AUTHENTICATION_REQUIRED)
            _set(states, reasons, CertificationState.EXECUTION_API_OK,
                 CertificationStatus.UNVERIFIED, SafeReason.CAPABILITY_METADATA_UNAVAILABLE)

    # Read-only checks cannot establish an order route or authorize live trading.
    for state in (CertificationState.EXECUTION_ROUTE_OK, CertificationState.LIVE_ELIGIBLE):
        _set(states, reasons, state, CertificationStatus.UNVERIFIED,
             SafeReason.EXTERNAL_CERTIFICATION_REQUIRED)

    if adapter is not None:
        try:
            await _within(adapter.close(), timeout_seconds)
        except Exception:
            # Teardown errors are intentionally not surfaced or allowed to mask evidence.
            pass

    return VenueAuthenticatedCertification(venue_id, states, reasons)


async def certify_authenticated_venues(
    credentials_by_venue: Mapping[str, Mapping[str, str] | None],
    *,
    adapter_factory: AdapterFactory,
    timeout_seconds: float = 10.0,
) -> dict[str, VenueAuthenticatedCertification]:
    """Run independent read-only certification for configured venue profiles.

    The factory receives ``(venue_id, credential_copy)`` and must return a
    ``VenueAdapter``. No default factory or real venue call is provided. Adapter
    exceptions are reduced to fixed reason codes; result objects never include
    credential values, field names, or exception messages. Only ``connect``,
    authentication/balance/permission/private-stream checks, capability
    metadata reads, and ``close`` are used. No create/cancel/order lookup API is
    called. Route and live states always remain unverified in this workflow.
    """
    if not isinstance(credentials_by_venue, Mapping):
        raise ValueError("credentials_by_venue must be a mapping")
    if not callable(adapter_factory):
        raise ValueError("adapter_factory is required")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    results: dict[str, VenueAuthenticatedCertification] = {}
    for venue_id, credentials in credentials_by_venue.items():
        if not isinstance(venue_id, str) or not venue_id.strip():
            continue
        try:
            results[venue_id] = await _certify_one(
                venue_id, credentials, adapter_factory, timeout_seconds
            )
        except Exception:
            # Isolate unexpected venue-local failures without leaking exception text.
            states = {state.value: CertificationStatus.UNVERIFIED.value for state in CertificationState}
            reasons = {
                CertificationState.AUTH_CONFIGURED.value: _credential_reason(credentials).value,
                CertificationState.AUTH_TRANSPORT_OK.value: SafeReason.ADAPTER_INITIALIZATION_FAILED.value,
                CertificationState.BALANCE_OK.value: SafeReason.AUTHENTICATION_REQUIRED.value,
                CertificationState.PERMISSIONS_OK.value: SafeReason.AUTHENTICATION_REQUIRED.value,
                CertificationState.PRIVATE_STREAM_OK.value: SafeReason.AUTHENTICATION_REQUIRED.value,
                CertificationState.EXECUTION_API_OK.value: SafeReason.CAPABILITY_METADATA_UNAVAILABLE.value,
                CertificationState.EXECUTION_ROUTE_OK.value: SafeReason.EXTERNAL_CERTIFICATION_REQUIRED.value,
                CertificationState.LIVE_ELIGIBLE.value: SafeReason.EXTERNAL_CERTIFICATION_REQUIRED.value,
            }
            results[venue_id] = VenueAuthenticatedCertification(venue_id, states, reasons)
    return results
