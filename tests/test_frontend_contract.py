from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
PUBLIC_HTML = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
RUN_ANALYSIS = json.loads(
    (ROOT / "diagnostics" / "engine-feed-persistence-analysis-latest.json").read_text(encoding="utf-8")
)


def test_frontend_uses_current_capital_policy():
    assert "$3 starter" in HTML
    assert "compound realized profit" in HTML
    assert "NO MARTINGALE" in HTML
    assert "FAIL-CLOSED" in HTML


def test_frontend_live_engine_requires_multi_venue_selection():
    assert 'id="engineVenue" multiple' in HTML
    assert 'selectedOptions].map' in HTML
    assert 'trade allocation' in HTML.lower()


def test_frontend_has_no_retired_pilot_copy():
    assert "max $25" not in HTML
    assert "max $3 pilot limit" not in HTML
    assert "18 configured public spot adapters" not in HTML


def test_frontend_exposes_operator_surfaces():
    for needle in ("#market", "#opportunities", "#accountPanel", "#engineBox"):
        assert needle in HTML


def test_both_frontends_expose_authenticated_account_recovery_and_remember_me():
    for page in (HTML, PUBLIC_HTML):
        assert 'id="accountPasswordConfirm"' in page
        assert 'id="rememberMe"' in page
        assert 'id="forgotPasswordButton"' in page
        assert 'id="recoveryForm"' in page
        assert 'id="resetPasswordForm"' in page
        assert 'remember_me: $("rememberMe").checked' in page
        assert "/api/v1/auth/password-reset/request" in page
        assert "/api/v1/auth/password-reset/confirm" in page


def test_both_frontends_expose_session_connection_depth_opportunity_and_trade_boards():
    for page in (HTML, PUBLIC_HTML):
        for needle in (
            'id="connectionRows"',
            'id="targetProgress"',
            'id="opportunityRows"',
            'id="journalRows"',
            'id="runAnalysisSummary"',
            'LAST SESSION',
            "A credential check is a point-in-time probe",
            "restLatencyMs",
            "visibleDepthQuote",
            "not a second continuous order-book stream",
            "arb_bot/arbx/market.py",
        ):
            assert needle in page


def test_persisted_feed_analysis_remains_fail_closed_and_does_not_claim_live_validation():
    assert RUN_ANALYSIS["assessmentType"] == "source_and_frontend_run_analysis"
    assert RUN_ANALYSIS["credentialsRead"] is False
    assert RUN_ANALYSIS["ordersSubmitted"] is False
    assert RUN_ANALYSIS["liveSessionObserved"] is False
    assert RUN_ANALYSIS["readiness"]["ordersAuthorized"] is False
    assert RUN_ANALYSIS["readiness"]["qualifiedLiveVenues"] == 0
    assert RUN_ANALYSIS["readiness"]["priorPersistedSnapshot"]["freshForCurrentAssessment"] is True
    audits = RUN_ANALYSIS["publicReadOnlyRevalidation"]
    assert audits["randomizedAllVenueAudit"]["venuesCatalogued"] == 20
    assert audits["randomizedAllVenueAudit"]["restAndWebSocketBooksPassed"] == 8
    assert audits["requiredVenuePairAudit"]["pair"] == "BTC/USDT"
    assert audits["requiredVenuePairAudit"]["passed"] == 3
    assert audits["engagementAndNetworkCatalogAudit"]["chainSettlementRoutesVerified"] == 0
    assert audits["multiPairRestSnapshot"]["directionsMeetingEstimatedEdgeFloor"] == 0
