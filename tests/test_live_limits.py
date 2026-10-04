import os
import pathlib
import asyncio
import queue
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx.config import Config, ExchangeCfg
from arbx.hub import Hub
from arbx.web_api import EngineStart


class LivePilotLimitTests(unittest.TestCase):
    def test_live_config_enforces_two_venues_and_three_dollar_loss_cap(self):
        cfg = Config(
            mode="live",
            exchanges=[ExchangeCfg(id="binance", api_key="a", secret="b"),
                       ExchangeCfg(id="bybit", api_key="c", secret="d")],
            cross_enabled=True,
            cross_live=True,
            target_profit_usd=200,
            max_loss_usd=3,
        )
        cfg.validate()

        cfg.max_loss_usd = 3.01
        with self.assertRaisesRegex(ValueError, "no more than the \\$3"):
            cfg.validate()

    def test_live_config_requires_explicit_cross_venue_opt_in(self):
        cfg = Config(
            mode="live",
            exchanges=[ExchangeCfg(id="binance", api_key="a", secret="b"),
                       ExchangeCfg(id="bybit", api_key="c", secret="d")],
            cross_enabled=True,
            cross_live=False,
            target_profit_usd=200,
        )
        with self.assertRaisesRegex(ValueError, "BOT_CROSS_LIVE=1"):
            cfg.validate()

    def test_live_api_rejects_loss_cap_above_three_dollars(self):
        with self.assertRaises(ValueError):
            EngineStart(mode="live", exchange_ids=["binance", "bybit"], trade_size_usd=5,
                        max_loss_usd=3.01, target_profit_usd=200, cross_live=True)

    def test_live_api_requires_explicit_cross_exchange_execution(self):
        with self.assertRaises(ValueError):
            EngineStart(mode="live", exchange_ids=["binance", "bybit"], trade_size_usd=5,
                        max_loss_usd=3, target_profit_usd=200, cross_live=False)

    def test_live_terminal_default_target_is_two_hundred_usd(self):
        with patch.dict(os.environ, {"BOT_MODE": "live", "BOT_TARGET_PROFIT_USD": "",
                                    "BOT_MAX_LOSS_USD": ""}, clear=False):
            cfg = Config.from_env()
        self.assertEqual(cfg.target_profit_usd, 200.0)
        self.assertEqual(cfg.max_loss_usd, 3.0)

    def test_live_hub_never_starts_a_subset_when_a_selected_venue_fails(self):
        class FakeWorker:
            def __init__(self, venue_id, error=None):
                self.id = venue_id
                self.lat = None
                self.prepare = AsyncMock(side_effect=error)
                self.close = AsyncMock()
                self.start = Mock()

        with tempfile.TemporaryDirectory() as directory:
            cfg = Config(
                mode="live",
                exchanges=[ExchangeCfg(id="binance"), ExchangeCfg(id="bybit")],
                cross_enabled=True,
                cross_live=True,
                target_profit_usd=200,
                max_loss_usd=3,
                journal_path=pathlib.Path(directory) / "trades.csv",
            )
            first = FakeWorker("binance")
            second = FakeWorker("bybit", RuntimeError("permission unavailable"))
            hub = Hub(cfg, queue.Queue())
            with patch("arbx.hub.ExchangeWorker", side_effect=[first, second]):
                asyncio.run(hub.run())

        first.start.assert_not_called()
        second.start.assert_not_called()
        self.assertEqual(hub.stats.status, "ERROR")


if __name__ == "__main__":
    unittest.main()
