from arbx.agentic.decision import assess_observation


def good_observation():
    return {
        "age_ms": 100,
        "net_edge_bps": 12.0,
        "books_synchronized": True,
        "risk_gate_passed": True,
        "venue_evidence_fresh": True,
    }


def test_valid_observation_is_advisory_only():
    result = assess_observation(good_observation(), min_net_edge_bps=5)
    assert result.status == "ADVISORY_PASS"
    assert not result.reasons
    assert "execution gates remain mandatory" in result.advisory_summary


def test_missing_evidence_fails_closed():
    result = assess_observation({})
    assert result.status == "REJECTED"
    assert "required_numeric_evidence_missing" in result.reasons


def test_malformed_numeric_evidence_fails_closed():
    obs = good_observation()
    obs["age_ms"] = "100"
    result = assess_observation(obs)
    assert result.status == "REJECTED"
    assert "invalid_numeric_evidence" in result.reasons


def test_non_finite_numeric_evidence_fails_closed():
    for invalid in (float("nan"), float("inf"), float("-inf")):
        obs = good_observation()
        obs["net_edge_bps"] = invalid
        result = assess_observation(obs)
        assert result.status == "REJECTED"
        assert "invalid_numeric_evidence" in result.reasons


def test_stale_book_is_rejected():
    obs = good_observation()
    obs["age_ms"] = 2000
    result = assess_observation(obs, max_age_ms=1000)
    assert result.status == "REJECTED"
    assert "stale_market_evidence" in result.reasons


def test_negative_age_is_rejected():
    obs = good_observation()
    obs["age_ms"] = -1
    result = assess_observation(obs)
    assert result.status == "REJECTED"
    assert "invalid_negative_age" in result.reasons


def test_agent_cannot_override_engine_risk_gate():
    obs = good_observation()
    obs["risk_gate_passed"] = False
    result = assess_observation(obs)
    assert result.status == "REJECTED"
    assert "engine_risk_gate_not_passed" in result.reasons


def test_boolean_numeric_values_are_not_accepted_as_evidence():
    obs = good_observation()
    obs["age_ms"] = True
    result = assess_observation(obs)
    assert result.status == "REJECTED"
    assert "invalid_numeric_evidence" in result.reasons
