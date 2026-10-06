from arbx.hybrid.networks import MAJOR_NETWORKS, network_id


def test_major_network_scope_contains_at_least_twenty_networks():
    assert len(MAJOR_NETWORKS) >= 20
    assert network_id("ERC20") == "ethereum"
    assert network_id("TRC20") == "tron"
    assert network_id("SPL") == "solana"
