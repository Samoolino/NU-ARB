"""Read-only, per-venue public market certification.

The workflow accepts already-constructed adapters and deliberately does not
connect, inspect credentials, or call account, private-stream, or order APIs.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping

from .venue_contract import (
    Capability,
    CapabilityReport,
    CapabilityStatus,
    MarketType,
    OrderBook,
    VenueAdapter,
)


@dataclass(frozen=True, slots=True)
class BookCheck:
    status: str
    timestamp_status: str
    sequence_status: str
    bid_levels: int
    ask_levels: int
    latency_ms: Decimal | None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class VenuePublicMarketCertification:
    venue_id: str
    symbol: str
    status: str
    market_discovery_status: str
    rest_order_book: BookCheck
    public_ws_status: str
    public_ws_order_book: BookCheck | None
    detail: str | None = None

    def catalog_evidence(self) -> dict:
        """Return only public evidence; never grants authenticated/live states."""
        return {
            "rest": self.market_discovery_status == "verified"
            and self.rest_order_book.status == "verified",
            "publicMarket": self.status == "verified",
            "publicWebSocket": self.public_ws_status == "verified",
            "executionCapabilities": {},
            "authentication": False,
            "balances": False,
            "privateWebSocket": False,
            "liveEligible": False,
        }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _levels_valid(book: OrderBook, minimum_depth: int) -> tuple[bool, int, int]:
    bid_count, ask_count = len(book.bids), len(book.asks)
    if bid_count < minimum_depth or ask_count < minimum_depth:
        return False, bid_count, ask_count
    try:
        for level in (*book.bids, *book.asks):
            price, quantity = Decimal(level.price), Decimal(level.quantity)
            if not price.is_finite() or not quantity.is_finite() or price <= 0 or quantity <= 0:
                return False, bid_count, ask_count
    except (InvalidOperation, TypeError, ValueError):
        return False, bid_count, ask_count
    return True, bid_count, ask_count


def _check_book(
    book: OrderBook,
    *,
    symbol: str,
    received_at: datetime,
    now: datetime,
    latency_ms: Decimal,
    maximum_age: timedelta,
    maximum_latency_ms: Decimal,
    minimum_depth: int,
    maximum_future_skew: timedelta,
) -> BookCheck:
    valid_depth, bid_count, ask_count = _levels_valid(book, minimum_depth)
    if book.symbol != symbol:
        return BookCheck("failed", "unverified", "unavailable" if book.sequence is None else "present",
                         bid_count, ask_count, latency_ms, "symbol_mismatch")
    if not valid_depth:
        return BookCheck("failed", "unverified", "unavailable" if book.sequence is None else "present",
                         bid_count, ask_count, latency_ms, "insufficient_or_invalid_depth")
    if latency_ms > maximum_latency_ms:
        return BookCheck("failed", "unverified", "unavailable" if book.sequence is None else "present",
                         bid_count, ask_count, latency_ms, "latency_limit_exceeded")
    if book.timestamp_ms is None:
        return BookCheck("unverified", "unavailable", "unavailable" if book.sequence is None else "present",
                         bid_count, ask_count, latency_ms, "venue_timestamp_missing")
    try:
        venue_time = datetime.fromtimestamp(book.timestamp_ms / 1000, tz=timezone.utc)
        local_received = _utc(received_at)
    except (OverflowError, OSError, TypeError, ValueError):
        return BookCheck("failed", "invalid", "unavailable" if book.sequence is None else "present",
                         bid_count, ask_count, latency_ms, "invalid_timestamp")
    reference_now = _utc(now)
    age = reference_now - venue_time
    receive_age = reference_now - local_received
    timestamp_status = (
        "stale"
        if age > maximum_age or receive_age > maximum_age
        else "future"
        if age < -maximum_future_skew or receive_age < -maximum_future_skew
        else "fresh"
    )
    if timestamp_status != "fresh":
        return BookCheck("failed", timestamp_status, "unavailable" if book.sequence is None else "present",
                         bid_count, ask_count, latency_ms, "book_timestamp_not_fresh")
    return BookCheck(
        "verified",
        "fresh",
        "present" if book.sequence is not None else "unavailable",
        bid_count,
        ask_count,
        latency_ms,
    )


def _declared(adapter: VenueAdapter, capability: Capability) -> CapabilityStatus:
    report = adapter.capabilities
    if not isinstance(report, CapabilityReport):
        return CapabilityStatus.UNVERIFIED
    return report.declaration_for(capability).status


async def _within(awaitable, timeout_seconds: float):
    return await asyncio.wait_for(awaitable, timeout=timeout_seconds)


async def certify_public_markets(
    adapters: Mapping[str, VenueAdapter],
    *,
    symbol: str,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.perf_counter,
    maximum_age: timedelta = timedelta(seconds=30),
    maximum_latency_ms: Decimal = Decimal("5000"),
    minimum_depth: int = 5,
    timeout_seconds: float = 10.0,
) -> dict[str, VenuePublicMarketCertification]:
    """Certify public REST market discovery/depth and optional public WS depth.

    No adapter factory, ``connect``, credential field, or non-public contract
    method is used. Each supplied venue is caught independently; exception
    text is intentionally omitted from returned results.
    """
    if not symbol or minimum_depth < 1 or timeout_seconds <= 0:
        raise ValueError("symbol, positive minimum_depth, and timeout are required")
    maximum_latency_ms = Decimal(maximum_latency_ms)
    if maximum_latency_ms < 0 or maximum_age < timedelta(0):
        raise ValueError("freshness and latency limits cannot be negative")

    results: dict[str, VenuePublicMarketCertification] = {}
    for venue_id, adapter in adapters.items():
        unavailable_book = BookCheck(
            "unverified", "unverified", "unverified", 0, 0, None, "not_checked"
        )
        discovery_status = "unverified"
        rest_check = unavailable_book
        ws_status = "unverified"
        ws_check = None
        detail = None
        try:
            required = (Capability.REST, Capability.MARKET_DISCOVERY, Capability.ORDER_BOOK)
            declarations = tuple(_declared(adapter, item) for item in required)
            if any(item is CapabilityStatus.UNSUPPORTED for item in declarations):
                results[venue_id] = VenuePublicMarketCertification(
                    venue_id, symbol, "unavailable", "unavailable", unavailable_book,
                    "unverified", None, "required_public_rest_capability_unsupported",
                )
                continue
            if any(item is not CapabilityStatus.IMPLEMENTED for item in declarations):
                results[venue_id] = VenuePublicMarketCertification(
                    venue_id, symbol, "unverified", "unverified", unavailable_book,
                    "unverified", None, "required_public_rest_capability_unverified",
                )
                continue

            markets = await _within(adapter.get_markets(), timeout_seconds)
            market = next(
                (
                    item for item in markets
                    if item.symbol == symbol
                    and item.market_type is MarketType.SPOT
                    and item.active is not False
                ),
                None,
            )
            if market is None:
                discovery_status = "failed"
                detail = "active_spot_market_not_found"
            else:
                discovery_status = "verified"
                started = monotonic()
                book = await _within(adapter.get_order_book(symbol, minimum_depth), timeout_seconds)
                elapsed = Decimal(str(max(0.0, monotonic() - started) * 1000))
                received = now()
                rest_check = _check_book(
                    book,
                    symbol=symbol,
                    received_at=received,
                    now=received,
                    latency_ms=elapsed,
                    maximum_age=maximum_age,
                    maximum_latency_ms=maximum_latency_ms,
                    minimum_depth=minimum_depth,
                    maximum_future_skew=timedelta(seconds=2),
                )
        except Exception:
            # Never expose adapter exception text, which may contain auth material.
            if discovery_status == "unverified":
                discovery_status = "failed"
                detail = "public_market_check_failed"
            elif rest_check.status == "unverified":
                rest_check = BookCheck(
                    "failed", "unverified", "unverified", 0, 0, None,
                    "public_rest_order_book_failed",
                )
                detail = "public_market_check_failed"

        try:
            ws_declarations = (
                _declared(adapter, Capability.PUBLIC_STREAM),
                _declared(adapter, Capability.ORDER_BOOK_STREAM),
            )
            if any(item is CapabilityStatus.UNSUPPORTED for item in ws_declarations):
                ws_status = "unavailable"
            elif any(item is not CapabilityStatus.IMPLEMENTED for item in ws_declarations):
                ws_status = "unverified"
            else:
                started = monotonic()
                stream = adapter.stream_order_book(symbol, minimum_depth)
                try:
                    book = await _within(anext(stream), timeout_seconds)
                finally:
                    close = getattr(stream, "aclose", None)
                    if close is not None:
                        await _within(close(), timeout_seconds)
                received = now()
                elapsed = Decimal(str(max(0.0, monotonic() - started) * 1000))
                ws_check = _check_book(
                    book,
                    symbol=symbol,
                    received_at=received,
                    now=received,
                    latency_ms=elapsed,
                    maximum_age=maximum_age,
                    maximum_latency_ms=maximum_latency_ms,
                    minimum_depth=minimum_depth,
                    maximum_future_skew=timedelta(seconds=2),
                )
                ws_status = "verified" if ws_check.status == "verified" else ws_check.status
        except Exception:
            # A public-stream failure never changes independently obtained REST evidence.
            ws_status = "failed"
            if ws_check is None:
                ws_check = BookCheck(
                    "failed", "unverified", "unverified", 0, 0, None,
                    "public_websocket_check_failed",
                )

        status = (
            "verified"
            if discovery_status == "verified" and rest_check.status == "verified"
            else "failed"
            if discovery_status == "failed" or rest_check.status == "failed"
            else "unverified"
        )
        results[venue_id] = VenuePublicMarketCertification(
            venue_id, symbol, status, discovery_status, rest_check, ws_status, ws_check, detail
        )
    return results
