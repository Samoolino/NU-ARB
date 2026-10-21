import pytest
from arbx.config import Config, ExchangeCfg


def live_cfg(**overrides):
    values = dict(mode="live", exchanges=[
        ExchangeCfg("binance", venue_id="binance", api_key="key", secret="secret"),
        ExchangeCfg("bybit", venue_id="bybit", api_key="key", secret="secret"),
    ], start_capital_usd=3.0, starter_capital_usd=3.0, trade_size_usd=0.0,
       cross_enabled=True, cross_live=True)
    values.update(overrides)
    return Config(**values)


def test_live_config_uses_balance_sizing_without_fixed_three_dollar_cap(monkeypatch):
    for key in ("BOT_TRADE_SIZE_USD","BOT_SESSION_CAPITAL_USD","BOT_TARGET_PROFIT_USD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BOT_MODE","live")
    monkeypatch.setenv("BOT_EXCHANGES","binance,bybit")
    monkeypatch.setenv("BOT_BINANCE_KEY","key"); monkeypatch.setenv("BOT_BINANCE_SECRET","secret")
    monkeypatch.setenv("BOT_BYBIT_KEY","key"); monkeypatch.setenv("BOT_BYBIT_SECRET","secret")
    monkeypatch.setenv("BOT_CROSS","1"); monkeypatch.setenv("BOT_CROSS_LIVE","1")
    cfg=Config.from_env()
    assert cfg.trade_size_usd == 0.0
    cfg.validate()
    assert cfg.halt_on_realized_loss is False


def test_optional_session_capital_must_be_positive():
    with pytest.raises(ValueError,match="BOT_SESSION_CAPITAL_USD"):
        live_cfg(session_capital_usd=0).validate()


def test_explicit_trade_size_is_an_engagement_cap():
    cfg=live_cfg(trade_size_usd=125.0,session_capital_usd=250.0)
    cfg.validate()
    assert cfg.trade_size_usd == 125.0 and cfg.session_capital_usd == 250.0


def test_paper_mode_rejects_zero_trade_size():
    cfg=Config(mode="paper",exchanges=[ExchangeCfg("binance")],trade_size_usd=0.0)
    with pytest.raises(ValueError,match="BOT_TRADE_SIZE_USD"):
        cfg.validate()
