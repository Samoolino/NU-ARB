import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.hub import Stats


def test_stats_snapshot_has_initialized_telemetry_fields():
    stats = Stats(session_id="test")
    snapshot = stats.snapshot()
    assert snapshot["venue_health"] == {}
    assert snapshot["market_universe"] == {}
