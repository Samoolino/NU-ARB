from __future__ import annotations

from arbx.venue_verification import VENUES, build_empty_report


def test_venue_report_covers_all_twenty_catalogue_entries():
    report = build_empty_report()
    rows = report["venues"]

    assert len(VENUES) == 20
    assert len(rows) == 20
    assert {row["venue_id"] for row in rows} == set(VENUES)


def test_missing_runtime_evidence_remains_unverified():
    report = build_empty_report()
    for row in report["venues"]:
        assert row["public_connectivity"] == "unverified"
        assert row["data_freshness"] == "unverified"
        assert row["reconnect_behavior"] == "unverified"
        assert row["permission_evidence"] == "unverified"
        assert row["evidence"] == []


def test_report_does_not_claim_connectivity_or_permissions_from_adapter_metadata():
    report = build_empty_report()
    assert report["summary"]["public_connectivity_verified"] == 0
    assert report["summary"]["reconnect_verified"] == 0
    assert report["summary"]["permission_verified"] == 0
    assert report["mode"] == "metadata_only"
