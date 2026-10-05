import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.config import Config, ExchangeCfg
from arbx.gate import RiskManager, ProfitGate


class ProfitCompoundingTests(unittest.TestCase):
    def live_cfg(self):
        return Config(
            mode="live",
            exchanges=[
                ExchangeCfg(id="binance", api_key="a", secret="b"),
                ExchangeCfg(id="bybit", api_key="c", secret="d"),
            ],
            cross_enabled=True,
            cross_live=True,
            trade_size_usd=25,
            max_loss_usd=3,
            target_profit_usd=200,
        )

    def test_live_normalizes_to_three_dollar_profit_dca(self):
        cfg = self.live_cfg()
        cfg.validate()
        self.assertEqual(cfg.starter_capital_usd, 3.0)
        self.assertEqual(cfg.trade_size_usd, 3.0)
        self.assertEqual(cfg.max_loss_usd, 0.0)
        self.assertIsNone(cfg.target_profit_usd)
        self.assertEqual(cfg.strategy_mode, "profit_dca")
        self.assertTrue(cfg.compound_profits)
        self.assertTrue(cfg.halt_on_realized_loss)

    def test_realized_profit_increases_next_trade_size(self):
        cfg = self.live_cfg()
        cfg.validate()
        risk = RiskManager(cfg)
        risk.record(0.12, True)
        self.assertAlmostEqual(cfg.trade_size_usd, 3.12, places=8)
        risk.record(0.18, True)
        self.assertAlmostEqual(cfg.trade_size_usd, 3.30, places=8)

    def test_loss_stops_live_engine_and_does_not_compound(self):
        cfg = self.live_cfg()
        cfg.validate()
        risk = RiskManager(cfg)
        risk.record(0.50, True)
        self.assertAlmostEqual(cfg.trade_size_usd, 3.50, places=8)
        risk.record(-0.01, True)
        self.assertTrue(risk.halted)
        self.assertIn("realized loss protection", risk.reason)
        self.assertAlmostEqual(cfg.trade_size_usd, 3.49, places=8)

    def test_profit_gate_rejects_non_positive_live_floor(self):
        cfg = self.live_cfg()
        cfg.validate()
        risk = RiskManager(cfg)
        gate = ProfitGate(cfg, risk)
        decision = gate._common(10, 10, 1, 0, True)
        self.assertFalse(decision.ok)
        self.assertEqual(decision.reason, "profit_below_min_usd")


if __name__ == "__main__":
    unittest.main()
