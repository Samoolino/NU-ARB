import pathlib
import re
import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "arb_bot"))

from arbx import web_api  # noqa: E402
from arbx.hybrid.registry import (  # noqa: E402
    VENUE_CATALOG,
    VENUE_IDS,
    VENUE_VERIFICATION_STATES,
    venue_registry_metadata,
)


EXPECTED_IDS = (
    "binance", "bybit", "okx", "kucoin", "gateio", "bitget", "kraken", "coinbase",
    "mexc", "htx", "bitfinex", "cryptocom", "coinex", "bitstamp", "gemini", "bingx",
    "lbank", "whitebit", "bitmart", "upbit",
)


def test_canonical_catalog_has_exact_identities_and_explicit_unverified_defaults():
    assert VENUE_IDS == EXPECTED_IDS
    assert len(VENUE_CATALOG) == 20
    for venue in VENUE_CATALOG:
        metadata = venue_registry_metadata(venue)
        assert venue.catalog_status == "CATALOGUED"
        assert venue.adapter_status == "UNVERIFIED"
        assert venue.authentication_status == "UNVERIFIED"
        assert venue.market_data_status == "UNVERIFIED"
        assert venue.execution_status == "UNVERIFIED"
        assert venue.live_eligibility_status == "UNVERIFIED"
        assert metadata["catalogState"] == "CATALOGUED"
        assert metadata["lifecycleState"] == "CATALOGUED"
        assert metadata["engineSelectionAvailable"] is False
        assert metadata["engineSelectionStatus"] == "UNAVAILABLE"
        assert metadata["verificationStates"] == {
            state: False for state in VENUE_VERIFICATION_STATES
        }


def test_frontend_catalog_matches_canonical_ids_but_keeps_catalog_only_venues_out_of_picker():
    source = (ROOT / "lib" / "exchange-registry.js").read_text(encoding="utf-8")
    frontend_ids = tuple(re.findall(r'venue\("([^"]+)"', source))
    assert frontend_ids == EXPECTED_IDS
    for venue_id in ("bitmart", "upbit"):
        assert f'venue("{venue_id}", "{venue_id}",' in source
    assert 'venue("coinbase", "coinbaseexchange",' in source
    for html_path in (ROOT / "index.html", ROOT / "public" / "index.html"):
        html = html_path.read_text(encoding="utf-8")
        assert "option.disabled = venue.engineSelectionSupported !== true" in html
        assert "venue.engineSelectionSupported &&" in html
        assert "venue.engineSelectionAvailable &&" in html


def test_verified_evidence_advances_only_observed_milestones():
    venue = VENUE_CATALOG[0]
    metadata = venue_registry_metadata(
        venue,
        evidence={
            "rest": True,
            "executionCapabilities": {"spotMarket": True},
            "publicWebSocket": True,
            "authentication": True,
            "balances": True,
            "privateWebSocket": True,
            "permissions": {"source": "venue key-scope endpoint"},
            "tradePermission": "enabled",
            "executionEligible": True,
            "liveEligible": False,
        },
        evidence_fresh=True,
        engine_selection_available=True,
    )
    assert metadata["lifecycleState"] == "PERMISSIONS_VERIFIED"
    assert metadata["verificationStates"]["PERMISSIONS_VERIFIED"] is True
    assert metadata["verificationStates"]["EXECUTION_ROUTE_VERIFIED"] is False
    assert metadata["verificationStates"]["LIVE_ELIGIBLE"] is False
    assert metadata["engineSelectionAvailable"] is True


def test_control_api_lists_catalog_metadata_but_keeps_unavailable_entries_unusable():
    class EmptyDatabase:
        def execute(self, *_args):
            return []

        def close(self):
            pass

    with patch.object(web_api, "_connect", return_value=EmptyDatabase()), \
            patch.object(web_api, "_current_user", return_value="test-user"):
        response = asyncio.run(web_api.exchanges(SimpleNamespace()))

    venues = response["exchanges"]
    assert tuple(venue["catalogId"] for venue in venues) == EXPECTED_IDS
    for venue in venues:
        assert venue["catalogState"] == "CATALOGUED"
        assert venue["lifecycleState"] == "CATALOGUED"
        assert venue["engineSelectionAvailable"] is False
        assert venue["engineSelectionStatus"] == "UNAVAILABLE"
        assert all(value is False for value in venue["verificationStates"].values())
    for venue_id in ("bitmart", "upbit"):
        venue = next(item for item in venues if item["catalogId"] == venue_id)
        assert venue["id"] == venue_id
        assert venue["engineSelectionSupported"] is False
        assert venue["adapterAvailable"] is False
        assert venue["authenticationModes"] == []
        assert venue["controlApiStatus"] == "UNAVAILABLE"


def test_unavailable_venues_are_rejected_by_engine_model_and_verify_route():
    for venue_id in ("bitmart", "upbit"):
        with pytest.raises(ValueError):
            web_api.EngineStart(
                mode="paper",
                exchange_ids=["binance", venue_id],
                trade_size_usd=5,
                max_loss_usd=2,
                target_profit_usd=1,
            )
        payload = web_api.ExchangeConnect(
            auth_mode="ccxt",
            credentials={"apiKey": "not-used", "secret": "not-used"},
            symbol="BTC/USDT",
        )
        with pytest.raises(HTTPException) as caught:
            asyncio.run(web_api.verify_exchange(venue_id, payload, None))
        assert caught.value.status_code == 404
