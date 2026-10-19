"""Fail-closed guard for explicitly disposable local PostgreSQL rehearsal targets.

This module validates target identifiers only; it does not connect to a database.
"""
from __future__ import annotations

from urllib.parse import urlsplit

_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "postgres"})
_ALLOWED_SCHEMES = frozenset({"postgres", "postgresql"})
_DISPOSABLE_MARKERS = ("test", "rehearsal", "disposable")


def _endpoint(url: str):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port or 5432
        database = parsed.path.lstrip("/")
    except (TypeError, ValueError):
        raise ValueError("PostgreSQL rehearsal target is invalid") from None

    if (
        parsed.scheme.lower() not in _ALLOWED_SCHEMES
        or host not in _ALLOWED_HOSTS
        or not database
        or "/" in database
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("PostgreSQL rehearsal target must be a local disposable test database")

    normalized_database = database.lower().replace("-", "_")
    if not any(marker in normalized_database.split("_") for marker in _DISPOSABLE_MARKERS):
        raise ValueError("PostgreSQL rehearsal target database name must identify a test, rehearsal, or disposable database")

    return host, port, normalized_database


def validate_disposable_target(target_url: str, production_url: str | None = None) -> bool:
    """Accept only local PostgreSQL databases explicitly named for disposable tests.

    The URL is never included in exceptions, so credentials and hostnames cannot
    leak into command output. If a production URL is supplied, matching endpoint
    identity is rejected even when credentials differ.
    """
    target_endpoint = _endpoint(target_url)
    if production_url:
        production_endpoint = _endpoint_for_comparison(production_url)
        if target_endpoint == production_endpoint:
            raise ValueError("PostgreSQL rehearsal target matches the configured production endpoint")
    return True


def _endpoint_for_comparison(url: str):
    """Parse a configured production URL for comparison without requiring test naming."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port or 5432
        database = parsed.path.lstrip("/").lower().replace("-", "_")
    except (TypeError, ValueError):
        raise ValueError("Configured production PostgreSQL endpoint is invalid") from None
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not host or not database:
        raise ValueError("Configured production PostgreSQL endpoint is invalid")
    return host, port, database
