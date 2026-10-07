import asyncio
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.hybrid.registry import VENUE_CATALOG, venue_registry_metadata  # noqa: E402
from arbx.public_market_certification import certify_public_markets  # noqa: E402
from arbx.venue_contract import (  # noqa: E402
    Capability,
    CapabilityDeclaration,
    CapabilityReport,
    CapabilityStatus,
    DepthLevel,
    MarketInfo,
    MarketType,
    OrderBook,
)


NOW = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
SYMBOL = "BTC/USDT"
DEFAULT_TIMESTAMP = object()


def fresh_book(symbol=SYMBOL, *, timestamp=DEFAULT_TIMESTAMP, bids=None, asks=None, sequence=17):
    return OrderBook(
        symbol=symbol,
        bids=tuple(bids if bids is not None else (
            DepthLevel(Decimal(100 - index), Decimal("1")) for index in range(5)
        )),
        asks=tuple(asks if asks is not None else (
            DepthLevel(Decimal(101 + index), Decimal("1")) for index in range(5)
        )),
        timestamp_ms=(
            int(NOW.timestamp() * 1000)
            if timestamp is DEFAULT_TIMESTAMP
            else timestamp
        ),
        sequence=sequence,
    )


class FakePublicAdapter:
    def __init__(self, *, websocket=False, market_error=None, book=None, ws_book=None):
        declarations = {
            Capability.REST: CapabilityDeclaration(CapabilityStatus.IMPLEMENTED),
            Capability.MARKET_DISCOVERY: CapabilityDeclaration(CapabilityStatus.IMPLEMENTED),
            Capability.ORDER_BOOK: CapabilityDeclaration(CapabilityStatus.IMPLEMENTED),
            Capability.PUBLIC_STREAM: CapabilityDeclaration(
                CapabilityStatus.IMPLEMENTED if websocket else CapabilityStatus.UNSUPPORTED
            ),
            Capability.ORDER_BOOK_STREAM: CapabilityDeclaration(
                CapabilityStatus.IMPLEMENTED if websocket else CapabilityStatus.UNSUPPORTED
            ),
        }
        self.capabilities = CapabilityReport(declarations=declarations)
        self.market_error = market_error
        self.book = book or fresh_book()
        self.ws_book = ws_book or fresh_book()
        self.calls = []
        self.websocket_error = None

    async def get_markets(self):
        self.calls.append("get_markets")
        if self.market_error:
            raise RuntimeError(self.market_error)
        return (MarketInfo(SYMBOL, "BTC", "USDT", MarketType.SPOT, active=True),)

    async def get_order_book(self, symbol, limit=20):
        self.calls.append(("get_order_book", symbol, limit))
        return self.book

    async def stream_order_book(self, symbol, limit=20):
        self.calls.append(("stream_order_book", symbol, limit))
        if self.websocket_error:
            raise RuntimeError(self.websocket_error)
        yield self.ws_book

    async def connect(self):
        raise AssertionError("certification must not connect or load credentials")

    async def get_balances(self):
        raise AssertionError("certification must not request balances")

    async def stream_private_events(self):
        raise AssertionError("certification must not subscribe to private events")

    async def create_order(self, request):
        raise AssertionError("certification must not place orders")

    async def cancel_order(self, order_id, symbol):
        raise AssertionError("certification must not cancel orders")


def run(adapters, *, monotonic=None):
    return asyncio.run(
        certify_public_markets(
            adapters,
            symbol=SYMBOL,
            now=lambda: NOW,
            monotonic=monotonic or (lambda: 100.0),
        )
    )


def test_rest_only_certification_checks_market_book_timestamp_sequence_depth_and_latency():
    adapter = FakePublicAdapter()
    result = run({"rest-only": adapter})["rest-only"]

    assert result.status == "verified"
    assert result.market_discovery_status == "verified"
    assert result.rest_order_book.status == "verified"
    assert result.rest_order_book.timestamp_status == "fresh"
    assert result.rest_order_book.sequence_status == "present"
    assert result.rest_order_book.bid_levels == result.rest_order_book.ask_levels == 5
    assert result.rest_order_book.latency_ms == Decimal("0.0")
    assert result.public_ws_status == "unavailable"
    assert adapter.calls == ["get_markets", ("get_order_book", SYMBOL, 5)]

    evidence = result.catalog_evidence()
    venue = next(item for item in VENUE_CATALOG if item.id == "binance")
    metadata = venue_registry_metadata(venue, evidence=evidence, evidence_fresh=True)
    assert metadata["lifecycleState"] == "PUBLIC_MARKET_VERIFIED"
    assert metadata["verificationStates"]["PUBLIC_MARKET_VERIFIED"] is True
    assert metadata["verificationStates"]["PUBLIC_WS_VERIFIED"] is False
    assert metadata["verificationStates"]["AUTHENTICATED"] is False
    assert metadata["verificationStates"]["BALANCE_VERIFIED"] is False
    assert metadata["verificationStates"]["PRIVATE_STREAM_VERIFIED"] is False
    assert metadata["verificationStates"]["LIVE_ELIGIBLE"] is False
    assert evidence["liveEligible"] is False


def test_public_ws_supported_records_first_orderbook_without_auth_or_execution_evidence():
    adapter = FakePublicAdapter(websocket=True)
    result = run({"ws": adapter})["ws"]

    assert result.status == "verified"
    assert result.public_ws_status == "verified"
    assert result.public_ws_order_book.status == "verified"
    assert result.public_ws_order_book.sequence_status == "present"
    assert adapter.calls[-1] == ("stream_order_book", SYMBOL, 5)
    evidence = result.catalog_evidence()
    metadata = venue_registry_metadata(
        next(item for item in VENUE_CATALOG if item.id == "bybit"),
        evidence=evidence,
        evidence_fresh=True,
    )
    assert metadata["lifecycleState"] == "PUBLIC_WS_VERIFIED"
    assert metadata["verificationStates"]["AUTHENTICATED"] is False
    assert metadata["verificationStates"]["EXECUTION_ROUTE_VERIFIED"] is False
    assert metadata["verificationStates"]["LIVE_ELIGIBLE"] is False


@pytest.mark.parametrize(
    ("book", "expected_status", "expected_timestamp"),
    [
        (fresh_book(timestamp=int((NOW - timedelta(minutes=2)).timestamp() * 1000)),
         "failed", "stale"),
        (fresh_book(bids=()), "failed", "unverified"),
        (fresh_book(timestamp=None), "unverified", "unavailable"),
    ],
)
def test_stale_or_missing_rest_book_cannot_pass(book, expected_status, expected_timestamp):
    result = run({"venue": FakePublicAdapter(book=book)})["venue"]
    assert result.status == expected_status
    assert result.rest_order_book.status == expected_status
    assert result.rest_order_book.timestamp_status == expected_timestamp
    assert result.catalog_evidence()["publicMarket"] is False


def test_missing_sequence_is_reported_and_excessive_latency_fails_certification():
    no_sequence = run({"venue": FakePublicAdapter(book=fresh_book(sequence=None))})["venue"]
    assert no_sequence.status == "verified"
    assert no_sequence.rest_order_book.sequence_status == "unavailable"

    clock_values = iter((10.0, 16.0))
    slow = asyncio.run(
        certify_public_markets(
            {"slow": FakePublicAdapter()},
            symbol=SYMBOL,
            now=lambda: NOW,
            monotonic=lambda: next(clock_values),
            maximum_latency_ms=Decimal("5000"),
        )
    )["slow"]
    assert slow.status == "failed"
    assert slow.rest_order_book.detail == "latency_limit_exceeded"
    assert slow.rest_order_book.latency_ms == Decimal("6000.0")


def test_venue_errors_are_isolated_and_exception_text_is_not_returned():
    secret = "apiKey=secret-value"
    failed = FakePublicAdapter(market_error=f"transport failed {secret}")
    healthy = FakePublicAdapter()
    results = run({"broken": failed, "healthy": healthy})

    assert results["broken"].status == "failed"
    assert results["broken"].detail == "public_market_check_failed"
    assert results["healthy"].status == "verified"
    assert secret not in repr(results["broken"])
    assert "secret-value" not in repr(results)


def test_optional_websocket_failure_does_not_reclassify_rest_book_or_leak_error_text():
    adapter = FakePublicAdapter(websocket=True, book=fresh_book(timestamp=None))
    adapter.websocket_error = "private-key=secret-value"
    result = run({"venue": adapter})["venue"]

    assert result.status == "unverified"
    assert result.rest_order_book.detail == "venue_timestamp_missing"
    assert result.public_ws_status == "failed"
    assert "secret-value" not in repr(result)


def test_unverified_public_capability_is_explicit_and_skips_calls():
    adapter = FakePublicAdapter()
    adapter.capabilities = CapabilityReport(declarations={
        Capability.REST: CapabilityDeclaration(CapabilityStatus.IMPLEMENTED),
        Capability.MARKET_DISCOVERY: CapabilityDeclaration(CapabilityStatus.UNVERIFIED),
        Capability.ORDER_BOOK: CapabilityDeclaration(CapabilityStatus.IMPLEMENTED),
    })
    result = run({"unknown": adapter})["unknown"]

    assert result.status == "unverified"
    assert result.market_discovery_status == "unverified"
    assert result.detail == "required_public_rest_capability_unverified"
    assert adapter.calls == []


def test_catalog_public_states_require_explicit_boolean_evidence():
    metadata = venue_registry_metadata(
        next(item for item in VENUE_CATALOG if item.id == "binance"),
        evidence={"publicMarket": "true", "publicWebSocket": 1},
        evidence_fresh=True,
    )
    assert metadata["lifecycleState"] == "CATALOGUED"
    assert metadata["verificationStates"]["PUBLIC_MARKET_VERIFIED"] is False
    assert metadata["verificationStates"]["PUBLIC_WS_VERIFIED"] is False
