"""PostgreSQL journal contract tests against the disposable CI database only."""
import os
import uuid

import pytest

from arbx.journal_storage import BackendTradeJournal
from arbx.postgres import run_postgres_migrations
from arbx.storage import PostgresBackend


@pytest.fixture
def journal():
    url = os.getenv("ARBX_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set ARBX_TEST_POSTGRES_URL for disposable PostgreSQL integration tests")
    if "localhost" not in url and "127.0.0.1" not in url and "postgres" not in url:
        pytest.fail("integration tests only accept a local/disposable PostgreSQL host")
    run_postgres_migrations(url)
    instance = BackendTradeJournal(PostgresBackend(url))
    yield instance
    instance.close()


def test_postgres_journal_round_trips_json_and_realized_fill_filter(journal):
    session_id = "journal-contract-" + uuid.uuid4().hex
    journal.append({
        "session_id": session_id, "mode": "simulation", "exchange_a": "fixture",
        "strategy": "contract-test", "net_pnl": 1.5, "execution_status": "FILLED",
        "verification_status": "verified", "cumulative_realized_pnl": 1.5,
        "path": ["fixture-a", "fixture-b"], "symbols": ["BTC/USDT"],
    })
    journal.append({
        "session_id": session_id, "mode": "simulation", "exchange_a": "fixture",
        "strategy": "contract-test", "net_pnl": 100, "execution_status": "REJECTED",
        "verification_status": "unverified", "cumulative_realized_pnl": 1.5,
    })
    assert journal.realized(session_id) == pytest.approx(1.5)

    journal.append_opportunity({
        "session_id": session_id, "mode": "simulation", "strategy": "contract-test",
        "exchange_a": "fixture", "symbol": "BTC/USDT", "requested_usd": 10,
        "expected_net_usd": 0.2, "worst_case_net_usd": -0.1,
        "expected_net_bps": 20, "worst_case_net_bps": -10,
        "book_age_ms": 10, "decision": "REJECT",
        "evidence": {"origin": "integration-test"},
    })
    opportunities = journal.recent_opportunities()
    matching = [row for row in opportunities if row["session_id"] == session_id]
    assert matching[0]["evidence"] == {"origin": "integration-test"}

    journal.record_certification(
        session_id=session_id, venue="fixture", symbol="BTC/USDT", notional_usd=10,
        live_eligible=False, reasons=["simulation_only"], evidence={"status": "unverified"},
    )
    certification = journal.latest_certification("fixture")
    assert certification is not None
    assert certification["live_eligible"] is False
    assert certification["reasons"] == ["simulation_only"]
    assert certification["evidence"] == {"status": "unverified"}
