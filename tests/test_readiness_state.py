import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "arb_bot"))

from arbx.readiness_state import build_readiness_state, write_readiness_state


def _write_fixture(directory, filename, report):
    (directory / filename).write_text(json.dumps(report), encoding="utf-8")


def _fresh_reports(directory, generated_at):
    stamp = generated_at.isoformat()
    _write_fixture(directory, "randomized-venue-feed-audit-latest.json", {
        "schemaVersion": 1,
        "auditType": "randomized_all_venue_public_feed_audit",
        "generatedAtUtc": stamp,
        "scope": "unauthenticated_public_spot_market_data_only",
        "persistenceValidation": {"validated": True},
        "venues": [
            {
                "venue": venue,
                "restOk": True,
                "websocketOk": True,
                "verified": True,
                "restRttMs": 12.5,
                "websocketFirstBookMs": 22.0,
                "depthPilot": {
                    "passed": True,
                    "notionalQuote": 25.0,
                    "quoteCurrency": "USDT",
                    "rest": {
                        "depthValidated": True,
                        "bookStructureValid": True,
                        "simulatedImmediateRoundTrip": {"completed": True},
                    },
                    "websocket": {
                        "depthValidated": True,
                        "bookStructureValid": True,
                        "simulatedImmediateRoundTrip": {"completed": True},
                    },
                    "websocketDepthUpdates": {
                        "verified": True,
                        "updateIntervalMs": {"p50": 5.0, "p95": 7.0},
                        "deliveryCallLatencyMs": {"p50": 1.0, "p95": 2.0},
                    },
                },
            }
            for venue in ("binance", "bybit", "kucoin")
        ],
    })
    _write_fixture(directory, "permission-revalidation-latest.json", {
        "schemaVersion": 1,
        "auditType": "authenticated_permission_revalidation",
        "updatedAtUtc": stamp,
        "scope": "read_only_authenticated_permission_probes",
        "persistenceValidation": {"validated": True},
        "venues": {
            venue: {
                "validationComplete": True,
                "liveEligible": True,
                "policyBlockers": [],
            }
            for venue in ("binance", "bybit", "kucoin")
        },
    })
    _write_fixture(directory, "authenticated-feed-validation-latest.json", {
        "schemaVersion": 1,
        "auditType": "authenticated_feed_validation",
        "updatedAtUtc": stamp,
        "scope": "authenticated_permission_and_read_only_same_pair_rest_websocket_books",
        "persistenceValidation": {"validated": True},
        "venues": {
            venue: {
                "permissionValidated": True,
                "restBookValid": True,
                "websocketBookValid": True,
                "strictPairValidated": True,
            }
            for venue in ("binance", "bybit", "kucoin")
        },
    })


def test_readiness_is_always_fail_closed_without_live_connection_evidence(tmp_path):
    now = datetime.now(timezone.utc)
    _fresh_reports(tmp_path, now)
    report = build_readiness_state(tmp_path, now=now)

    assert report["readinessState"] == "BLOCKED_LIVE_EVIDENCE_MISSING"
    assert report["liveEngageable"] is False
    assert report["credentialsRead"] is False
    assert report["qualifiedLiveVenueCount"] == 0
    requirement = report["liveModeRequirement"]
    assert requirement["targetMode"] == "live"
    assert requirement["readinessState"] == "BLOCKED_LIVE_EVIDENCE_MISSING"
    assert requirement["authorized"] is False
    assert requirement["ordersEnabled"] is False
    assert requirement["minimumDistinctLiveEligibleVenues"] == 2
    assert requirement["currentlyVerifiedVenues"] == []
    assert "market_specific_ioc_execution_route_and_fee_tier" in requirement["requiredEvidence"]
    assert all(source["status"] == "FRESH" for source in report["evidenceSources"].values())
    for venue in report["venues"]:
        if venue["livePermissionCandidate"]:
            assert venue["publicOrderbookDepth"]["verified"] is True
            assert venue["publicOrderbookDepth"]["restBookStructureValid"] is True
            assert venue["publicOrderbookDepth"]["websocketBookStructureValid"] is True
            assert venue["latencyEvidence"]["status"] == "OBSERVED_ONLY"
            assert venue["latencyEvidence"]["restRttMs"] == 12.5
            assert venue["latencyEvidence"]["restRttSampleCount"] == 1
            assert venue["latencyEvidence"]["liveLatencyGatePassed"] is False
            assert venue["permissionEvidence"]["liveEligible"] is True
            assert venue["accountBalancesVerified"] is False
            assert venue["privateStreamVerified"] is False
            assert venue["executionRouteVerified"] is False
            assert venue["riskAndCapitalApproved"] is False
            assert venue["liveEligible"] is False


def test_readiness_marks_missing_public_depth_pilot_as_blocked(tmp_path):
    now = datetime.now(timezone.utc)
    _fresh_reports(tmp_path, now)
    public_path = tmp_path / "randomized-venue-feed-audit-latest.json"
    public_report = json.loads(public_path.read_text(encoding="utf-8"))
    public_report["venues"][0].pop("depthPilot")
    public_path.write_text(json.dumps(public_report), encoding="utf-8")

    report = build_readiness_state(tmp_path, now=now)
    binance = next(row for row in report["venues"] if row["venue"] == "binance")

    assert binance["publicOrderbookDepth"]["status"] == "NOT_RECORDED"
    assert binance["publicOrderbookDepth"]["verified"] is False
    assert "fresh_public_orderbook_depth_not_recorded" in binance["blockers"]
    assert report["liveModeRequirement"]["ordersEnabled"] is False


def test_readiness_rejects_depth_pilot_without_both_book_walks(tmp_path):
    now = datetime.now(timezone.utc)
    _fresh_reports(tmp_path, now)
    public_path = tmp_path / "randomized-venue-feed-audit-latest.json"
    public_report = json.loads(public_path.read_text(encoding="utf-8"))
    public_report["venues"][0]["depthPilot"]["websocket"][
        "simulatedImmediateRoundTrip"
    ]["completed"] = False
    public_path.write_text(json.dumps(public_report), encoding="utf-8")

    report = build_readiness_state(tmp_path, now=now)
    binance = next(row for row in report["venues"] if row["venue"] == "binance")

    assert binance["publicOrderbookDepth"]["status"] == "FAILED"
    assert binance["publicOrderbookDepth"]["verified"] is False


def test_readiness_rejects_missing_asynchronous_websocket_updates(tmp_path):
    now = datetime.now(timezone.utc)
    _fresh_reports(tmp_path, now)
    public_path = tmp_path / "randomized-venue-feed-audit-latest.json"
    public_report = json.loads(public_path.read_text(encoding="utf-8"))
    public_report["venues"][0]["depthPilot"].pop("websocketDepthUpdates")
    public_path.write_text(json.dumps(public_report), encoding="utf-8")

    report = build_readiness_state(tmp_path, now=now)
    binance = next(row for row in report["venues"] if row["venue"] == "binance")

    assert binance["publicOrderbookDepth"]["status"] == "FAILED"
    assert binance["publicOrderbookDepth"]["asynchronousWebsocketUpdatesVerified"] is False


def test_readiness_marks_expired_and_malformed_evidence_unusable(tmp_path):
    now = datetime.now(timezone.utc)
    _fresh_reports(tmp_path, now - timedelta(minutes=16))
    public_path = tmp_path / "randomized-venue-feed-audit-latest.json"
    public_path.write_text("{bad-json", encoding="utf-8")

    report = build_readiness_state(tmp_path, now=now)

    assert report["evidenceSources"]["randomizedPublicFeedAudit"]["status"] == "INVALID"
    assert report["evidenceSources"]["permissionRevalidation"]["status"] == "STALE"
    assert report["evidenceSources"]["authenticatedSamePairFeedValidation"]["status"] == "STALE"
    assert report["liveEngageable"] is False
    assert report["qualifiedLiveVenueCount"] == 0


def test_readiness_rejects_wrong_scope_and_naive_timestamps(tmp_path):
    now = datetime.now(timezone.utc)
    _fresh_reports(tmp_path, now)
    public_path = tmp_path / "randomized-venue-feed-audit-latest.json"
    public_report = json.loads(public_path.read_text(encoding="utf-8"))
    public_report["scope"] = "authenticated_account_data"
    public_path.write_text(json.dumps(public_report), encoding="utf-8")
    permission_path = tmp_path / "permission-revalidation-latest.json"
    permission_report = json.loads(permission_path.read_text(encoding="utf-8"))
    permission_report["updatedAtUtc"] = now.replace(tzinfo=None).isoformat()
    permission_path.write_text(json.dumps(permission_report), encoding="utf-8")

    report = build_readiness_state(tmp_path, now=now)

    assert report["evidenceSources"]["randomizedPublicFeedAudit"]["status"] == "INVALID"
    assert report["evidenceSources"]["permissionRevalidation"]["status"] == "INVALID"


def test_readiness_is_atomically_persisted_as_latest_and_timestamped_report(tmp_path):
    now = datetime.now(timezone.utc)
    report = write_readiness_state(tmp_path, now=now)

    latest = tmp_path / "live-readiness-state-latest.json"
    persisted = json.loads(latest.read_text(encoding="utf-8"))
    timestamped = pathlib.Path(report["persistenceValidation"]["artifactPaths"][0])

    assert timestamped.is_file()
    assert persisted == report
    assert persisted["persistenceValidation"]["validated"] is True
    assert persisted["liveEngageable"] is False
    assert persisted["liveModeRequirement"]["ordersEnabled"] is False
    assert persisted["ordersSubmitted"] is False


def test_strategy_catalog_distinguishes_live_paths_from_catalog_labels(tmp_path):
    report = build_readiness_state(tmp_path)
    strategies = {item["id"]: item for item in report["strategies"]}

    assert strategies["cross_exchange"]["livePath"] == "implemented_fail_closed"
    assert strategies["cross_exchange"]["liveEngagementVerified"] is False
    assert strategies["triangular_intra_exchange"]["livePath"] == "implemented_fail_closed"
    assert strategies["stablecoin_arbitrage"]["livePath"] == "no_dedicated_route"
    assert strategies["triangular_multi_exchange"]["livePath"] == "unsupported"
    assert strategies["dca_profit_compounding"]["kind"] == "capital_policy"
    assert strategies["spot"]["kind"] == "market_type"
