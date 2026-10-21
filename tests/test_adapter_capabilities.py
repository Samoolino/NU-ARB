import pytest
from arbx.hybrid.adapters import CCXTAdapter


@pytest.mark.asyncio
async def test_rest_fallback_does_not_claim_websocket_capabilities():
    adapter=CCXTAdapter("binance","binance")
    adapter.ex=type("FakeExchange",(),{"has":{
        "fetchTicker":True,"fetchOrderBook":True,"createOrder":True,
        "fetchBalance":True,"watchOrderBook":False,"watchBalance":False,
    }})()
    caps=await adapter.get_capabilities()
    assert caps.rest and caps.ccxt
    assert not caps.websocket and not caps.user_stream and not caps.balance_stream
