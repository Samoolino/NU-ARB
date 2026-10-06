from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")


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
