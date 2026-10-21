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

    def test_live_keeps_explicit_trade_cap_and_removes_loss_stop(self):
        cfg = self.live_cfg()
        cfg.validate()
        self.assertEqual(cfg.starter_capital_usd, 3.0)
        self.assertEqual(cfg.trade_size_usd, 25.0)
        self.assertEqual(cfg.max_loss_usd, 0.0)
        self.assertEqual(cfg.target_profit_usd, 200.0)
        self.assertEqual(cfg.strategy_mode, "profit_dca")
        self.assertTrue(cfg.compound_profits)
        self.assertFalse(cfg.halt_on_realized_loss)

    def test_realized_profit_increases_next_trade_size(self):
        cfg = self.live_cfg()
        cfg.validate()
        risk = RiskManager(cfg)
        risk.record(0.12, True)
        self.assertAlmostEqual(cfg.trade_size_usd, 25.0, places=8)
        risk.record(0.18, True)
        self.assertAlmostEqual(cfg.trade_size_usd, 25.0, places=8)

    def test_realized_loss_does_not_stop_or_mutate_balance_based_size(self):
        cfg = self.live_cfg()
        cfg.validate()
        risk = RiskManager(cfg)
        risk.record(0.50, True)
        self.assertAlmostEqual(cfg.trade_size_usd, 25.0, places=8)
        risk.record(-0.01, True)
        self.assertFalse(risk.halted)
        self.assertAlmostEqual(risk.pnl, 0.49, places=8)
        self.assertAlmostEqual(cfg.trade_size_usd, 25.0, places=8)

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
