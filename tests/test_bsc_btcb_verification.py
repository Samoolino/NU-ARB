import pytest
from arbx.hybrid.bsc_btcb import BSCBTCBAdapter, BTCBRouteManifest, PINNED_MANIFEST_SHA256


@pytest.mark.asyncio
async def test_btcb_route_is_not_live_eligible_without_manifest():
    result = await BSCBTCBAdapter("", None).verify()
    assert not result.live_eligible
    assert "route_manifest_missing" in result.reasons


@pytest.mark.asyncio
async def test_unpinned_manifest_fails_closed_before_rpc():
    manifest = BTCBRouteManifest(56, "0x"+"1"*40, "0x"+"2"*40,
                                 "0x"+"3"*40, "0x"+"4"*40, "0x"+"5"*40, 18)
    result = await BSCBTCBAdapter("http://127.0.0.1:8545", manifest).verify(simulation_ok=True)
    assert not result.live_eligible
    assert "trusted_manifest_not_pinned" in result.reasons
    assert result.evidence["transaction_submission"] is False


@pytest.mark.asyncio
async def test_hybrid_engine_exposes_verification_only_route():
    from arbx.hybrid.engine import HybridEngine
    engine = HybridEngine(cfg=None, adapters={}, evidence={}, capabilities={})
    result = await engine.verify_btcb_route("", None)
    assert not result.live_eligible
    assert "route_manifest_missing" in result.reasons
