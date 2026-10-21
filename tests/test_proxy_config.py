import pytest
from types import SimpleNamespace

from arbx.config import Config
from arbx.worker import configure_proxy


def test_proxy_config_applies_to_rest_and_websocket():
    exchange = SimpleNamespace()
    result = configure_proxy(exchange, "http://proxy.example:8080")
    assert result is exchange
    assert exchange.aiohttp_proxy == "http://proxy.example:8080"
    assert exchange.ws_proxy == "http://proxy.example:8080"
    assert exchange.wss_proxy == "http://proxy.example:8080"


def test_invalid_proxy_scheme_fails_closed():
    with pytest.raises(ValueError, match="proxy URL"):
        configure_proxy(SimpleNamespace(), "file:///etc/passwd")


def test_proxy_can_be_configured_per_exchange_or_globally(monkeypatch):
    monkeypatch.setenv("BOT_MODE", "paper")
    monkeypatch.setenv("BOT_EXCHANGES", "binance,bybit")
    monkeypatch.setenv("BOT_PROXY_URL", "http://proxy.example:8080")
    monkeypatch.setenv("BOT_BYBIT_PROXY_URL", "socks5h://proxy.example:1080")
    cfg = Config.from_env()
    assert cfg.exchanges[0].proxy_url == "http://proxy.example:8080"
    assert cfg.exchanges[1].proxy_url == "socks5h://proxy.example:1080"
