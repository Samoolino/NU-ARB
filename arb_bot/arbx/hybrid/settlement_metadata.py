"""Offline parsing and isolated storage for venue-reported settlement metadata.

This module consumes structured API data supplied by its caller. It performs no
network requests and does not enable or submit withdrawals or transfers.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from arbx.hybrid.networks import network_id


SUPPORTED_ASSETS = frozenset(
    {"USDT", "USDC", "BTC", "ETH", "BNB", "SOL", "AVAX", "POL", "ARB", "OP", "TRX"}
)
ASSET_ALIASES = {"MATIC": "POL"}
_NETWORK_FIELDS = ("network", "networkId", "id", "chain")
_MISSING = object()
_INVALID = object()
_INTEGER_PATTERN = re.compile(r"^[0-9]+$")


def canonical_asset(value: object) -> str | None:
    """Return an explicitly supported canonical asset, or ``None`` if unknown."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    normalized = ASSET_ALIASES.get(normalized, normalized)
    return normalized if normalized in SUPPORTED_ASSETS else None


@dataclass(frozen=True, slots=True)
class VenueSettlementMetadata:
    """One network row backed only by metadata present in a venue report."""

    venue_id: str
    asset: str
    network_id: str
    deposit_enabled: bool | None = None
    withdrawal_enabled: bool | None = None
    fee: float | None = None
    minimum: float | None = None
    confirmations: int | None = None
    contract_address: str | None = None

    @property
    def venue_report_status(self) -> str:
        return "VENUE_REPORTED"

    @property
    def transfer_status(self) -> str:
        return "UNVERIFIED"

    @property
    def withdrawal_operation_enabled(self) -> bool:
        """Venue metadata never authorizes a withdrawal operation."""
        return False

    @property
    def transfer_operation_enabled(self) -> bool:
        """Venue metadata never authorizes a transfer operation."""
        return False


def _resolve_field(metadata: Mapping[str, Any], keys: tuple[str, ...], parser):
    """Parse aliases; malformed or disagreeing supplied values remain unknown."""
    supplied = [metadata[key] for key in keys if key in metadata]
    if not supplied:
        return None, False
    values = []
    for raw_value in supplied:
        value = parser(raw_value)
        if value is _INVALID:
            return None, True
        values.append(value)
    if any(value != values[0] for value in values[1:]):
        return None, True
    return values[0], False


def _parse_bool(value: object):
    if isinstance(value, bool):
        return value
    return _INVALID


def _parse_nonnegative_number(value: object):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return _INVALID
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return _INVALID
    if not decimal_value.is_finite() or decimal_value < 0:
        return _INVALID
    result = float(decimal_value)
    return result if math.isfinite(result) else _INVALID


def _parse_confirmations(value: object):
    if isinstance(value, bool):
        return _INVALID
    if isinstance(value, int):
        return value if value >= 0 else _INVALID
    if isinstance(value, str) and _INTEGER_PATTERN.fullmatch(value.strip()):
        return int(value.strip())
    return _INVALID


def _parse_contract(value: object):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return _INVALID
    return value.strip()


def _network_metadata(
    raw_network: str,
    metadata: Mapping[str, Any],
) -> tuple[str | None, dict[str, Any], set[str]]:
    """Extract only explicitly supplied, well-formed fields from one network row."""
    explicit = [metadata[field] for field in _NETWORK_FIELDS if field in metadata]
    if explicit:
        canonical_ids = []
        for value in explicit:
            canonical = network_id(value)
            if canonical is None:
                return None, {}, set()
            canonical_ids.append(canonical)
        if len(set(canonical_ids)) != 1:
            return None, {}, set()
        canonical_network = canonical_ids[0]
    else:
        canonical_network = network_id(raw_network)
        if canonical_network is None:
            return None, {}, set()

    specifications = {
        "deposit_enabled": (("deposit", "depositEnabled", "deposit_enabled", "depositEnable"), _parse_bool),
        "withdrawal_enabled": (
            (
                "withdraw",
                "withdrawal",
                "withdrawEnabled",
                "withdraw_enabled",
                "withdrawalEnabled",
                "withdrawal_enabled",
                "withdrawEnable",
            ),
            _parse_bool,
        ),
        "fee": (("fee", "withdrawFee", "withdraw_fee"), _parse_nonnegative_number),
        "minimum": (("minimum", "min", "withdrawMin", "withdraw_min"), _parse_nonnegative_number),
        "confirmations": (
            ("confirmations", "confirmation", "requiredConfirmations"),
            _parse_confirmations,
        ),
        "contract_address": (
            ("contract", "contractAddress", "contract_address"),
            _parse_contract,
        ),
    }
    values: dict[str, Any] = {}
    invalid_fields: set[str] = set()
    for field, (aliases, parser) in specifications.items():
        value, invalid = _resolve_field(metadata, aliases, parser)
        values[field] = value
        if invalid:
            invalid_fields.add(field)

    # CCXT-style reports often put the withdrawal minimum under limits.withdraw.min.
    limits = metadata.get("limits", _MISSING)
    if isinstance(limits, Mapping):
        withdraw_limits = limits.get("withdraw", _MISSING)
        if isinstance(withdraw_limits, Mapping) and "min" in withdraw_limits:
            nested_minimum = _parse_nonnegative_number(withdraw_limits["min"])
            if nested_minimum is _INVALID:
                values["minimum"] = None
                invalid_fields.add("minimum")
            elif "minimum" in invalid_fields:
                values["minimum"] = None
            elif values["minimum"] is not None and values["minimum"] != nested_minimum:
                values["minimum"] = None
                invalid_fields.add("minimum")
            else:
                values["minimum"] = nested_minimum

    return canonical_network, values, invalid_fields


def _merge_network_rows(
    accumulated: dict[str, Any],
    incoming: dict[str, Any],
    invalid_fields: set[str],
    accumulated_invalid: set[str],
) -> None:
    """Merge aliases for one canonical dimension without resolving conflicts by order."""
    for field, incoming_value in incoming.items():
        if field in invalid_fields:
            accumulated[field] = None
            accumulated_invalid.add(field)
            continue
        if field in accumulated_invalid or incoming_value is None:
            continue
        current = accumulated[field]
        if current is None:
            accumulated[field] = incoming_value
        elif current != incoming_value:
            accumulated[field] = None
            accumulated_invalid.add(field)


def parse_venue_settlement_metadata(
    venue_id: str,
    currency_report: object,
) -> tuple[VenueSettlementMetadata, ...]:
    """Parse supported asset/network metadata from a supplied currency report.

    The accepted structure is ``asset -> {"networks": {network: metadata}}``.
    Unknown assets and networks are ignored. Network flags and costs are retained
    only when their supplied values are valid and unambiguous; missing, malformed,
    or conflicting values remain ``None``. No missing field is inferred.
    """
    if not isinstance(venue_id, str) or not venue_id.strip():
        return ()
    if not isinstance(currency_report, Mapping):
        return ()

    normalized_venue = venue_id.strip().casefold()
    collected: dict[tuple[str, str], tuple[dict[str, Any], set[str]]] = {}
    for raw_asset, currency in currency_report.items():
        asset = canonical_asset(raw_asset)
        if asset is None or not isinstance(currency, Mapping):
            continue
        raw_networks = currency.get("networks")
        if not isinstance(raw_networks, Mapping):
            continue
        for raw_key, metadata in raw_networks.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or not isinstance(metadata, Mapping):
                continue
            network, values, invalid_fields = _network_metadata(raw_key, metadata)
            if network is None:
                continue
            key = (asset, network)
            if key not in collected:
                collected[key] = (values, set(invalid_fields))
                continue
            accumulated, accumulated_invalid = collected[key]
            _merge_network_rows(accumulated, values, invalid_fields, accumulated_invalid)

    results = []
    for (asset, network), (values, _) in sorted(collected.items()):
        results.append(
            VenueSettlementMetadata(
                venue_id=normalized_venue,
                asset=asset,
                network_id=network,
                **values,
            )
        )
    return tuple(results)


class SettlementMetadataRepository:
    """Repository over a caller-provided, dedicated SQLite connection.

    The repository never opens or discovers a database path. Callers must supply
    an isolated connection (tests use ``sqlite3.connect(":memory:")``), not the
    execution journal connection.
    """

    def __init__(self, connection: sqlite3.Connection):
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("a dedicated SQLite connection is required")
        self._db = connection
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS venue_settlement_metadata (
                venue_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                network_id TEXT NOT NULL,
                deposit_enabled INTEGER,
                withdrawal_enabled INTEGER,
                fee REAL,
                minimum REAL,
                confirmations INTEGER,
                contract_address TEXT,
                observed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                PRIMARY KEY (venue_id, asset, network_id)
            )"""
        )
        self._db.commit()

    @staticmethod
    def _validated_values(record: VenueSettlementMetadata) -> tuple[Any, ...]:
        if not isinstance(record, VenueSettlementMetadata):
            raise TypeError("expected VenueSettlementMetadata")
        venue = record.venue_id.strip().casefold() if isinstance(record.venue_id, str) else ""
        asset = canonical_asset(record.asset)
        network = network_id(record.network_id)
        if not venue or asset != record.asset or network != record.network_id:
            raise ValueError("invalid venue, asset, or canonical network")
        if asset is None or network is None:
            raise ValueError("unsupported asset or network")

        for value in (record.deposit_enabled, record.withdrawal_enabled):
            if value is not None and not isinstance(value, bool):
                raise ValueError("enabled metadata must be boolean or unknown")
        for value in (record.fee, record.minimum):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError("fee and minimum must be finite nonnegative numbers or unknown")
        if record.confirmations is not None and (
            isinstance(record.confirmations, bool)
            or not isinstance(record.confirmations, int)
            or record.confirmations < 0
        ):
            raise ValueError("confirmations must be a nonnegative integer or unknown")
        if record.contract_address is not None and _parse_contract(record.contract_address) != record.contract_address:
            raise ValueError("contract address must be a nonempty safe string or unknown")
        return (
            venue,
            asset,
            network,
            None if record.deposit_enabled is None else int(record.deposit_enabled),
            None if record.withdrawal_enabled is None else int(record.withdrawal_enabled),
            record.fee,
            record.minimum,
            record.confirmations,
            record.contract_address,
        )

    def upsert(self, record: VenueSettlementMetadata) -> None:
        values = self._validated_values(record)
        with self._db:
            self._db.execute(
                """INSERT INTO venue_settlement_metadata (
                       venue_id, asset, network_id, deposit_enabled, withdrawal_enabled,
                       fee, minimum, confirmations, contract_address
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (venue_id, asset, network_id) DO UPDATE SET
                       deposit_enabled=excluded.deposit_enabled,
                       withdrawal_enabled=excluded.withdrawal_enabled,
                       fee=excluded.fee,
                       minimum=excluded.minimum,
                       confirmations=excluded.confirmations,
                       contract_address=excluded.contract_address,
                       observed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                values,
            )

    def upsert_many(self, records: tuple[VenueSettlementMetadata, ...]) -> None:
        validated = [self._validated_values(record) for record in records]
        with self._db:
            for values in validated:
                self._db.execute(
                    """INSERT INTO venue_settlement_metadata (
                           venue_id, asset, network_id, deposit_enabled, withdrawal_enabled,
                           fee, minimum, confirmations, contract_address
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (venue_id, asset, network_id) DO UPDATE SET
                           deposit_enabled=excluded.deposit_enabled,
                           withdrawal_enabled=excluded.withdrawal_enabled,
                           fee=excluded.fee,
                           minimum=excluded.minimum,
                           confirmations=excluded.confirmations,
                           contract_address=excluded.contract_address,
                           observed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                    values,
                )

    def get(
        self,
        venue_id: str,
        asset: str,
        network: str,
    ) -> VenueSettlementMetadata | None:
        """Read one exact venue/asset/network row; never combine venue reports."""
        normalized_venue = venue_id.strip().casefold() if isinstance(venue_id, str) else ""
        normalized_asset = canonical_asset(asset)
        normalized_network = network_id(network)
        if not normalized_venue or normalized_asset is None or normalized_network is None:
            return None
        row = self._db.execute(
            """SELECT venue_id, asset, network_id, deposit_enabled, withdrawal_enabled,
                      fee, minimum, confirmations, contract_address
               FROM venue_settlement_metadata
               WHERE venue_id=? AND asset=? AND network_id=?""",
            (normalized_venue, normalized_asset, normalized_network),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_for_venue(self, venue_id: str) -> tuple[VenueSettlementMetadata, ...]:
        """Read only rows reported for the requested venue."""
        normalized_venue = venue_id.strip().casefold() if isinstance(venue_id, str) else ""
        if not normalized_venue:
            return ()
        rows = self._db.execute(
            """SELECT venue_id, asset, network_id, deposit_enabled, withdrawal_enabled,
                      fee, minimum, confirmations, contract_address
               FROM venue_settlement_metadata
               WHERE venue_id=? ORDER BY asset, network_id""",
            (normalized_venue,),
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _from_row(row) -> VenueSettlementMetadata:
        return VenueSettlementMetadata(
            venue_id=row[0],
            asset=row[1],
            network_id=row[2],
            deposit_enabled=None if row[3] is None else bool(row[3]),
            withdrawal_enabled=None if row[4] is None else bool(row[4]),
            fee=row[5],
            minimum=row[6],
            confirmations=row[7],
            contract_address=row[8],
        )
