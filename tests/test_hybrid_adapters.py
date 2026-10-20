import asyncio
from types import SimpleNamespace

from arbx.hybrid.adapters import CCXTAdapter


def test_ccxt_rest_fallback_never_claims_websocket_capabilities():
    adapter = CCXTAdapter("example", "example")
    adapter.ex = SimpleNamespace(
        has={
            "fetchTicker": True,
            "watchOrderBook": True,
            "watchBalance": True,
            "fetchOrderBook": True,
            "fetchBalance": True,
        }
    )

    capabilities = asyncio.run(adapter.get_capabilities())

    assert capabilities.rest
    assert capabilities.ccxt
    assert not capabilities.ccxt_pro
    assert not capabilities.websocket
    assert not capabilities.native_websocket
    assert not capabilities.user_stream
    assert not capabilities.balance_stream
