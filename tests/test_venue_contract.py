from datetime import datetime, timezone
from decimal import Decimal
from typing import get_type_hints

from arbx.venue_contract import (
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
    ExecutionCertification,
    MarketDepth,
    MarketInfo,
    MarketType,
    Order,
    OrderBook,
    OrderKind,
    OrderRequest,
    OrderState,
    Permission,
    PermissionAssessment,
    PermissionsEvidence,
    Position,
    Side,
    Ticker,
    VenueAdapter,
    VenueIdentity,
)


def test_capability_declaration_is_not_runtime_evidence():
    report = CapabilityReport()
    assert report.declaration_for(Capability.ORDER_CREATE).status is CapabilityStatus.UNVERIFIED
    assert report.evidence_for(Capability.ORDER_CREATE).status is EvidenceStatus.NOT_CHECKED

    report = CapabilityReport(
        declarations={
            Capability.TICKER: CapabilityDeclaration(CapabilityStatus.IMPLEMENTED),
            Capability.WITHDRAWAL: CapabilityDeclaration(CapabilityStatus.UNSUPPORTED),
        },
        evidence={
            Capability.TICKER: EvidenceRecord(EvidenceStatus.FAILED, detail="offline"),
        },
    )
    assert report.declaration_for(Capability.TICKER).status is CapabilityStatus.IMPLEMENTED
    assert report.evidence_for(Capability.TICKER).status is EvidenceStatus.FAILED
    assert report.declaration_for(Capability.WITHDRAWAL).status is CapabilityStatus.UNSUPPORTED
    assert report.evidence_for(Capability.WITHDRAWAL).status is EvidenceStatus.NOT_CHECKED


def test_capability_report_copies_and_freezes_supplied_mappings():
    declarations = {
        Capability.REST: CapabilityDeclaration(CapabilityStatus.IMPLEMENTED),
    }
    report = CapabilityReport(declarations=declarations)
    declarations.clear()
    assert report.declaration_for(Capability.REST).status is CapabilityStatus.IMPLEMENTED


def test_protocol_exposes_typed_operations_for_all_contract_areas():
    expected = {
        "identity", "capabilities", "connect", "close",
        "get_markets", "get_market", "get_ticker", "get_order_book", "get_depth",
        "stream_order_book", "stream_private_events", "get_balances", "get_positions",
        "get_open_orders", "get_order", "create_order", "cancel_order", "get_fees",
        "get_funding_rate", "get_deposit_address", "withdraw", "transfer",
        "verify_rest", "verify_public_stream", "verify_private_stream", "get_health",
        "verify_authentication", "verify_permissions", "certify_execution",
    }
    assert all(hasattr(VenueAdapter, name) for name in expected)
    assert get_type_hints(VenueAdapter.get_balances)["return"] == tuple[Balance, ...]
    assert get_type_hints(VenueAdapter.get_positions)["return"] == tuple[Position, ...]
    assert get_type_hints(VenueAdapter.get_ticker)["return"] is Ticker
    assert get_type_hints(VenueAdapter.get_depth)["return"] is MarketDepth
    assert get_type_hints(VenueAdapter.verify_rest)["return"] is ConnectivityEvidence
    assert get_type_hints(VenueAdapter.verify_authentication)["return"] is AuthenticationEvidence
    assert get_type_hints(VenueAdapter.verify_permissions)["return"] is PermissionsEvidence
    assert get_type_hints(VenueAdapter.certify_execution)["return"] is ExecutionCertification


def test_normalized_contract_models_retain_decimal_precision_and_evidence():
    checked_at = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
    identity = VenueIdentity("venue-x", "Venue X", "adapter-x", "rest+websocket")
    market = MarketInfo("BTC/USD", "BTC", "USD", MarketType.SPOT, active=True)
    book = OrderBook("BTC/USD", (), (), timestamp_ms=123, sequence=7)
    depth = MarketDepth(book, checked_at)
    request = OrderRequest("BTC/USD", Side.BUY, OrderKind.LIMIT, Decimal("0.125"))
    order = Order("order-1", request.symbol, request.side, request.kind, OrderState.OPEN, request.quantity)
    auth = AuthenticationEvidence(EvidenceRecord(EvidenceStatus.VERIFIED, checked_at), method="venue-native")
    permissions = PermissionsEvidence(
        (PermissionAssessment(Permission.TRADE, EvidenceRecord(EvidenceStatus.FAILED, checked_at)),),
        checked_at,
    )
    certification = ExecutionCertification(
        EvidenceRecord(EvidenceStatus.NOT_CHECKED), request.symbol, findings=("not assessed",)
    )
    connection = ConnectivityEvidence(ConnectionChannel.REST, EvidenceStatus.VERIFIED, checked_at)

    assert identity.display_name == "Venue X"
    assert market.market_type is MarketType.SPOT
    assert depth.book is book
    assert order.quantity == Decimal("0.125")
    assert auth.evidence.status is EvidenceStatus.VERIFIED
    assert permissions.assessments[0].evidence.status is EvidenceStatus.FAILED
    assert certification.evidence.status is EvidenceStatus.NOT_CHECKED
    assert connection.channel is ConnectionChannel.REST
