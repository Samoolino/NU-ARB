"""Persistent, fail-closed summary of the latest runtime verification evidence."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbx.hybrid.registry import VENUE_CATALOG
from arbx.hybrid.strategies import strategy_execution_catalog
from arbx.permissions import LIVE_PERMISSION_VERIFICATION_VENUES, PERMISSION_PROBE_VENUES

SCHEMA_VERSION = 1
EVIDENCE_TTL = timedelta(minutes=15)
LIVE_CANDIDATE_VENUES = tuple(sorted(LIVE_PERMISSION_VERIFICATION_VENUES))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _read_evidence(
    report_dir: Path,
    filename: str,
    timestamp_field: str,
    audit_type: str,
    scope: str,
) -> dict:
    path = report_dir / filename
    if not path.exists():
        return {"status": "MISSING", "generatedAtUtc": None, "ageSeconds": None}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"status": "INVALID", "generatedAtUtc": None, "ageSeconds": None}
    if not isinstance(report, dict):
        return {"status": "INVALID", "generatedAtUtc": None, "ageSeconds": None}
    timestamp = report.get(timestamp_field)
    if not isinstance(timestamp, str):
        return {"status": "INVALID", "generatedAtUtc": None, "ageSeconds": None}
    validation = report.get("persistenceValidation")
    if (
        report.get("schemaVersion") != 1
        or report.get("auditType") != audit_type
        or report.get("scope") != scope
        or not isinstance(validation, dict)
        or validation.get("validated") is not True
    ):
        return {"status": "INVALID", "generatedAtUtc": None, "ageSeconds": None}
    try:
        generated_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return {"status": "INVALID", "generatedAtUtc": None, "ageSeconds": None}
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        return {"status": "INVALID", "generatedAtUtc": None, "ageSeconds": None}
    return {
        "status": "LOADED",
        "generatedAtUtc": generated_at.astimezone(timezone.utc).isoformat(),
        "ageSeconds": None,
        "_generatedAt": generated_at.astimezone(timezone.utc),
        "_report": report,
    }


def _freshness(evidence: dict, now: datetime) -> dict:
    generated_at = evidence.pop("_generatedAt", None)
    report = evidence.pop("_report", None)
    if evidence["status"] != "LOADED":
        return evidence
    age = (now - generated_at).total_seconds()
    evidence["ageSeconds"] = round(age, 1)
    evidence["status"] = (
        "FRESH" if 0 <= age <= EVIDENCE_TTL.total_seconds() else "STALE"
    )
    evidence["_report"] = report
    return evidence


def _venue_source(report: dict | None, venue_id: str) -> dict | None:
    if not isinstance(report, dict):
        return None
    for row in report.get("venues", []):
        if isinstance(row, dict) and row.get("venue") == venue_id:
            return row
    row = report.get("venues", {}).get(venue_id) if isinstance(report.get("venues"), dict) else None
    return row if isinstance(row, dict) else None


def _evidence_summary(source: dict, row: dict | None, positive_fields: tuple[str, ...]) -> dict:
    if source["status"] != "FRESH":
        return {"status": source["status"], "verified": False}
    if not isinstance(row, dict):
        return {"status": "NOT_RECORDED", "verified": False}
    verified = all(row.get(field) is True for field in positive_fields)
    return {"status": "VERIFIED" if verified else "FAILED", "verified": verified}


def _safe_permission_summary(source: dict, row: dict | None) -> dict:
    if source["status"] != "FRESH":
        return {"status": source["status"], "verified": False, "liveEligible": False, "blockers": []}
    if not isinstance(row, dict):
        return {"status": "NOT_RECORDED", "verified": False, "liveEligible": False, "blockers": []}
    blockers = row.get("policyBlockers", row.get("blockers", []))
    if not isinstance(blockers, list) or not all(isinstance(item, str) for item in blockers):
        blockers = ["invalid_permission_blockers"]
    complete = row.get("validationComplete") is True
    eligible = complete and row.get("liveEligible") is True
    return {
        "status": "VERIFIED" if complete else "UNVERIFIED",
        "verified": complete,
        "liveEligible": eligible,
        "blockers": blockers,
    }


def build_readiness_state(report_dir: Path, *, now: datetime | None = None) -> dict:
    """Summarize existing reports without credentials, exchange requests, or order activity."""
    now = now or _utcnow()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    report_dir = Path(report_dir)

    public = _freshness(_read_evidence(
        report_dir,
        "randomized-venue-feed-audit-latest.json",
        "generatedAtUtc",
        "randomized_all_venue_public_feed_audit",
        "unauthenticated_public_spot_market_data_only",
    ), now)
    permissions = _freshness(_read_evidence(
        report_dir,
        "permission-revalidation-latest.json",
        "updatedAtUtc",
        "authenticated_permission_revalidation",
        "read_only_authenticated_permission_probes",
    ), now)
    strict_feed = _freshness(_read_evidence(
        report_dir,
        "authenticated-feed-validation-latest.json",
        "updatedAtUtc",
        "authenticated_feed_validation",
        "authenticated_permission_and_read_only_same_pair_rest_websocket_books",
    ), now)

    public_report = public.pop("_report", None)
    permission_report = permissions.pop("_report", None)
    strict_report = strict_feed.pop("_report", None)
    venue_rows = []
    for spec in VENUE_CATALOG:
        venue_id = spec.id
        public_row = _venue_source(public_report, venue_id)
        permission_row = _venue_source(permission_report, venue_id)
        strict_row = _venue_source(strict_report, venue_id)
        public_state = _evidence_summary(
            public,
            public_row,
            ("restOk", "websocketOk", "verified"),
        )
        if public["status"] != "FRESH":
            latency_state = {
                "status": public["status"],
                "marketDiscoveryMs": None,
                "restRttMs": None,
                "websocketFirstBookMs": None,
                "websocketUpdateIntervalMs": None,
                "websocketDeliveryCallLatencyMs": None,
                "restRttSampleCount": 0,
                "liveLatencyGatePassed": False,
            }
        elif not isinstance(public_row, dict):
            latency_state = {
                "status": "NOT_RECORDED",
                "marketDiscoveryMs": None,
                "restRttMs": None,
                "websocketFirstBookMs": None,
                "websocketUpdateIntervalMs": None,
                "websocketDeliveryCallLatencyMs": None,
                "restRttSampleCount": 0,
                "liveLatencyGatePassed": False,
            }
        else:
            raw_pilot = public_row.get("depthPilot")
            pilot_updates = (
                raw_pilot.get("websocketDepthUpdates")
                if isinstance(raw_pilot, dict)
                else None
            )
            latency_state = {
                "status": "OBSERVED_ONLY",
                "marketDiscoveryMs": public_row.get("marketLoadMs"),
                "restRttMs": public_row.get("restRttMs"),
                "websocketFirstBookMs": public_row.get("websocketFirstBookMs"),
                "websocketUpdateIntervalMs": (
                    pilot_updates.get("updateIntervalMs")
                    if isinstance(pilot_updates, dict)
                    else None
                ),
                "websocketDeliveryCallLatencyMs": (
                    pilot_updates.get("deliveryCallLatencyMs")
                    if isinstance(pilot_updates, dict)
                    else None
                ),
                "restRttSampleCount": (
                    1
                    if isinstance(public_row.get("restRttMs"), (int, float))
                    else 0
                ),
                "liveLatencyGatePassed": False,
            }
        public_depth_row = (
            public_row.get("depthPilot") if isinstance(public_row, dict) else None
        )
        if public["status"] != "FRESH":
            public_depth_state = {
                "status": public["status"],
                "verified": False,
            }
        elif not isinstance(public_depth_row, dict):
            public_depth_state = {"status": "NOT_RECORDED", "verified": False}
        else:
            rest_depth = public_depth_row.get("rest")
            websocket_depth = public_depth_row.get("websocket")
            websocket_updates = public_depth_row.get("websocketDepthUpdates")
            depth_verified = (
                public_depth_row.get("passed") is True
                and isinstance(rest_depth, dict)
                and rest_depth.get("depthValidated") is True
                and rest_depth.get("bookStructureValid") is True
                and isinstance(rest_depth.get("simulatedImmediateRoundTrip"), dict)
                and rest_depth["simulatedImmediateRoundTrip"].get("completed") is True
                and isinstance(websocket_depth, dict)
                and websocket_depth.get("depthValidated") is True
                and websocket_depth.get("bookStructureValid") is True
                and isinstance(websocket_depth.get("simulatedImmediateRoundTrip"), dict)
                and websocket_depth["simulatedImmediateRoundTrip"].get("completed") is True
                and isinstance(websocket_updates, dict)
                and websocket_updates.get("verified") is True
            )
            public_depth_state = {
                "status": (
                    "VERIFIED"
                    if depth_verified
                    else "FAILED"
                ),
                "verified": depth_verified,
                "restDepthValidated": (
                    rest_depth.get("depthValidated") is True
                    if isinstance(rest_depth, dict)
                    else False
                ),
                "restBookStructureValid": (
                    rest_depth.get("bookStructureValid") is True
                    if isinstance(rest_depth, dict)
                    else False
                ),
                "websocketDepthValidated": (
                    websocket_depth.get("depthValidated") is True
                    if isinstance(websocket_depth, dict)
                    else False
                ),
                "websocketBookStructureValid": (
                    websocket_depth.get("bookStructureValid") is True
                    if isinstance(websocket_depth, dict)
                    else False
                ),
                "asynchronousWebsocketUpdatesVerified": (
                    websocket_updates.get("verified") is True
                    if isinstance(websocket_updates, dict)
                    else False
                ),
                "websocketUpdateIntervalMs": (
                    websocket_updates.get("updateIntervalMs")
                    if isinstance(websocket_updates, dict)
                    else None
                ),
                "websocketDeliveryCallLatencyMs": (
                    websocket_updates.get("deliveryCallLatencyMs")
                    if isinstance(websocket_updates, dict)
                    else None
                ),
                "notionalQuote": public_depth_row.get("notionalQuote"),
                "quoteCurrency": public_depth_row.get("quoteCurrency"),
                "profitAssurance": False,
                "chainSettlementVerified": False,
            }
        permission_state = _safe_permission_summary(permissions, permission_row)
        strict_state = _evidence_summary(
            strict_feed,
            strict_row,
            ("permissionValidated", "restBookValid", "websocketBookValid", "strictPairValidated"),
        )
        is_live_candidate = venue_id in LIVE_CANDIDATE_VENUES
        blockers = []
        if not is_live_candidate:
            blockers.append("venue_not_in_strict_live_permission_candidate_policy")
        if not public_state["verified"]:
            blockers.append(f"fresh_public_rest_and_websocket_books_{public_state['status'].lower()}")
        if not public_depth_state["verified"]:
            blockers.append(f"fresh_public_orderbook_depth_{public_depth_state['status'].lower()}")
        blockers.append("multi_sample_runtime_live_latency_gate_not_verified")
        if not permission_state["verified"]:
            blockers.append(f"fresh_account_permission_evidence_{permission_state['status'].lower()}")
        if permission_state["verified"] and not permission_state["liveEligible"]:
            blockers.extend(permission_state["blockers"] or ["venue_permission_policy_blocks_live"])
        if not strict_state["verified"]:
            blockers.append(f"fresh_authenticated_same_pair_rest_websocket_{strict_state['status'].lower()}")
        blockers.extend((
            "authenticated_balance_and_reservations_not_verified_by_readiness_report",
            "private_user_stream_not_verified_by_readiness_report",
            "ioc_execution_route_not_certified_by_readiness_report",
            "risk_and_capital_approval_not_verified_by_readiness_report",
        ))
        venue_rows.append({
            "venue": venue_id,
            "livePermissionCandidate": is_live_candidate,
            "publicFeed": public_state,
            "publicOrderbookDepth": public_depth_state,
            "latencyEvidence": latency_state,
            "permissionEvidence": permission_state,
            "authenticatedSamePairFeeds": strict_state,
            "accountBalancesVerified": False,
            "privateStreamVerified": False,
            "executionRouteVerified": False,
            "riskAndCapitalApproved": False,
            "liveEligible": False,
            "blockers": list(dict.fromkeys(blockers)),
        })

    strategies = []
    for item in strategy_execution_catalog():
        strategy = dict(item)
        strategy["connectionRequirements"] = list(item["connectionRequirements"])
        if item["livePath"] == "implemented_fail_closed":
            strategy["connectionState"] = "BLOCKED_NO_FRESH_LIVE_VENUE_CERTIFICATION"
            strategy["liveEngagementVerified"] = False
            strategy["blockers"] = [
                "fresh_per_account_permission_feed_balance_stream_and_route_certification_required",
                "live_execution_switches_are_not_readiness_evidence",
            ]
        elif item["livePath"] == "policy_only":
            strategy["connectionState"] = "NOT_AN_EXECUTION_STRATEGY"
            strategy["liveEngagementVerified"] = False
            strategy["blockers"] = ["capital_policy_does_not_submit_orders"]
        elif item["livePath"] == "no_dedicated_route":
            strategy["connectionState"] = "NO_DEDICATED_LIVE_ROUTE"
            strategy["liveEngagementVerified"] = False
            strategy["blockers"] = ["helper_calculation_is_not_a_separately_certified_execution_path"]
        elif item["livePath"] == "unsupported":
            strategy["connectionState"] = "UNSUPPORTED_DISTINCT_EXECUTION_PATH"
            strategy["liveEngagementVerified"] = False
            strategy["blockers"] = ["catalog_label_has_no_distinct_multi_exchange_triangular_executor"]
        else:
            strategy["connectionState"] = "NOT_AN_ARBITRAGE_STRATEGY"
            strategy["liveEngagementVerified"] = False
            strategy["blockers"] = ["spot_is_a_market_type_not_an_opportunity_strategy"]
        strategies.append(strategy)

    live_venues = [row for row in venue_rows if row["livePermissionCandidate"]]
    qualified_live_venues = [
        row for row in live_venues
        if row["publicFeed"]["verified"]
        and row["publicOrderbookDepth"]["verified"]
        and row["latencyEvidence"]["liveLatencyGatePassed"]
        and row["permissionEvidence"]["verified"]
        and row["permissionEvidence"]["liveEligible"]
        and row["authenticatedSamePairFeeds"]["verified"]
        and row["accountBalancesVerified"]
        and row["privateStreamVerified"]
        and row["executionRouteVerified"]
        and row["riskAndCapitalApproved"]
    ]
    reasons = []
    if len(qualified_live_venues) < 2:
        reasons.append("two_distinct_fresh_account_certified_venues_required_for_cross_exchange_live")
    reasons.extend((
        "balances_private_stream_execution_route_and_runtime_risk_are_not_consumed_by_this_report",
        "no_strategy_is_declared_live_engagement_verified_by_catalog_or_public_feed_evidence",
    ))
    candidate_rows = [
        row for row in venue_rows if row["livePermissionCandidate"]
    ]
    live_mode_requirement = {
        "targetMode": "live",
        "readinessState": "READY" if qualified_live_venues else "BLOCKED_LIVE_EVIDENCE_MISSING",
        "authorized": False,
        "ordersEnabled": False,
        "requiredStrategy": "cross_exchange",
        "minimumDistinctLiveEligibleVenues": 2,
        "eligibleVenueCandidates": list(LIVE_CANDIDATE_VENUES),
        "currentlyVerifiedVenues": [
            row["venue"] for row in candidate_rows if row["liveEligible"]
        ],
        "requiredEvidence": [
            "fresh_public_rest_and_websocket_books",
            "fresh_public_orderbook_depth_and_simulation",
            "multi_sample_runtime_live_latency_gate",
            "fresh_account_permission_revalidation",
            "authenticated_same_pair_rest_and_websocket_books",
            "available_balances_and_order_reservations",
            "private_user_stream",
            "market_specific_ioc_execution_route_and_fee_tier",
            "asset_identity_and_settlement_route",
            "runtime_risk_and_capital_approval",
        ],
        "blockingReasons": list(dict.fromkeys(reasons)),
        "explanation": (
            "This is the persisted target policy, not a switch that enables trading. "
            "Public feed success cannot substitute for account, balance, private-stream, "
            "execution-route, settlement, or risk evidence."
        ),
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAtUtc": now.isoformat(),
        "readinessState": "BLOCKED_LIVE_EVIDENCE_MISSING",
        "liveEngageable": False,
        "ordersSubmitted": False,
        "transfersSubmitted": False,
        "credentialsRead": False,
        "evidenceFreshnessSeconds": int(EVIDENCE_TTL.total_seconds()),
        "evidenceSources": {
            "randomizedPublicFeedAudit": public,
            "permissionRevalidation": permissions,
            "authenticatedSamePairFeedValidation": strict_feed,
        },
        "liveCandidateVenues": list(LIVE_CANDIDATE_VENUES),
        "qualifiedLiveVenueCount": len(qualified_live_venues),
        "liveModeRequirement": live_mode_requirement,
        "venues": venue_rows,
        "strategies": strategies,
        "blockingReasons": reasons,
        "clarification": (
            "Strategy catalog membership, static execution code, and public feed checks do not "
            "verify a live connection. Per-account balances, private streams, permissions, "
            "market-specific IOC routes, fees, risk approval, and fresh same-pair data must be "
            "certified independently. This report is observational and cannot authorize orders."
        ),
    }


def _persist(report: dict, report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(report["generatedAtUtc"]).strftime("%Y%m%dT%H%M%SZ")
    paths = (
        report_dir / f"live-readiness-state-{stamp}.json",
        report_dir / "live-readiness-state-latest.json",
    )
    report["persistenceValidation"] = {
        "validated": False,
        "method": "atomic_write_then_json_readback",
        "artifactPaths": [str(path) for path in paths],
    }
    report["persistenceValidation"]["validated"] = True
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    for path in paths:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=report_dir,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        persisted = json.loads(path.read_text(encoding="utf-8"))
        if persisted != report or persisted.get("liveEngageable") is not False:
            raise OSError(f"live readiness report failed read-back validation: {path.name}")


def write_readiness_state(report_dir: Path, *, now: datetime | None = None) -> dict:
    report = build_readiness_state(report_dir, now=now)
    _persist(report, Path(report_dir))
    return report
