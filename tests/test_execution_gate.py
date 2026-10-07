import asyncio
import os
import pathlib
import sys
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.execution_gate import (
    ExecutionAuthorizationError,
    ExecutionEvidence,
    ExecutionGate,
    ExecutionOrder,
    OrderScope,
    opportunity_fingerprint,
)
from arbx.execute import CrossExecutor, LegFailure, LiveExecutor
from arbx.hybrid.adapters import AdapterError, CCXTProAdapter
from arbx.hybrid.contracts import OrderRequest


def live_environment(**overrides):
    values = {
        name: "1" for name in os.environ
        if name.startswith("BOT_ALLOW_")
    }
    values.update({
        "BOT_MODE": "live",
        "BOT_ALLOW_ORDERS": "1",
        "ARBX_LIVE_TRADING_ENABLED": "1",
        "BOT_CROSS_LIVE": "1",
        "BOT_TRIANGULAR_LIVE": "1",
    })
    values.update(overrides)
    return patch.dict(os.environ, values, clear=False)


def valid_evidence(**overrides):
    values = {
        "authenticated_credentials": True,
        "venue_live_eligible": True,
        "execution_eligible": True,
        "active_market": True,
        "fresh_orderbook": True,
        "sufficient_depth": True,
        "sufficient_balance": True,
        "sufficient_capital": True,
        "known_fees": True,
        "latency_acceptable": True,
        "risk_approved": True,
        "rate_limit_ok": True,
        "venue_healthy": True,
        "route_certified": True,
        "expected_pnl": 1.0,
        "min_expected_pnl": 0.01,
        "worst_case_pnl": 0.5,
        "min_worst_case_pnl": 0.01,
        "expected_bps": 10.0,
        "min_expected_bps": 3.0,
        "worst_case_bps": 5.0,
        "min_worst_case_bps": 0.5,
        "orderbook_observed_at": time.monotonic(),
        "max_book_age_ms": 10_000.0,
    }
    values.update(overrides)
    return ExecutionEvidence(**values)


def cross_opportunity():
    return SimpleNamespace(
        buy_ex="buy", sell_ex="sell", symbol="BTC/USDT", base=0.5,
        limit_buy=100.0, limit_sell=102.0, cost=50.0,
        expected_usd=1.0, worst_usd=0.5, net_bps=10.0, worst_bps=5.0,
        quote_ccy="USDT",
    )


def cross_permit(gate, opportunity=None):
    opportunity = opportunity or cross_opportunity()
    return gate.authorize(
        "cross",
        venue_ids=("buy", "sell"),
        route_id="cross:buy>sell",
        opportunity_id=opportunity_fingerprint("cross", opportunity),
        evidence=valid_evidence(),
        orders=(
            OrderScope(0, "buy", "BTC/USDT", "buy", "limit", 0.5, 100.0),
            OrderScope(1, "sell", "BTC/USDT", "sell", "limit", 0.5, 102.0),
        ),
    )


class FakeExchange:
    def __init__(self, filled=1.0):
        self.filled = filled
        self.orders = []

    def amount_to_precision(self, symbol, amount):
        return str(amount)

    def market(self, symbol):
        return {"base": "BTC", "quote": "USDT"}

    async def create_order(self, symbol, order_type, side, amount, price, params=None):
        self.orders.append((symbol, order_type, side, amount, price))
        return {
            "id": str(len(self.orders)),
            "filled": amount if self.filled is None else self.filled,
            "status": "closed",
            "cost": amount * (price or 100.0),
            "fees": [],
        }


class ExecutionGateTests(unittest.TestCase):
    def test_paper_mode_is_never_authorized(self):
        with live_environment():
            with self.assertRaisesRegex(ExecutionAuthorizationError, "paper_mode"):
                ExecutionGate(SimpleNamespace(mode="paper")).authorize("cross")

    def test_exact_process_and_operator_switches_are_required(self):
        cfg = SimpleNamespace(mode="live", cross_live=True, triangular_live=False)
        for name, bad_value, reason in (
            ("BOT_MODE", "LIVE", "BOT_MODE_not_live"),
            ("BOT_ALLOW_ORDERS", "true", "BOT_ALLOW_ORDERS_not_enabled"),
            ("ARBX_LIVE_TRADING_ENABLED", "0", "ARBX_LIVE_TRADING_ENABLED_not_enabled"),
            ("BOT_CROSS_LIVE", "0", "BOT_CROSS_LIVE_not_enabled"),
        ):
            with self.subTest(name=name), live_environment(**{name: bad_value}):
                with self.assertRaisesRegex(ExecutionAuthorizationError, reason):
                    cross_permit(ExecutionGate(cfg))

    def test_unverified_strategies_fail_closed_and_cross_does_not_authorize_triangle(self):
        with live_environment():
            gate = ExecutionGate(SimpleNamespace(mode="live", cross_live=True, triangular_live=False))
            with self.assertRaisesRegex(ExecutionAuthorizationError, "triangular_live_not_selected"):
                gate.authorize("triangular")
            with self.assertRaisesRegex(ExecutionAuthorizationError, "unsupported_execution_strategy"):
                gate.authorize("hybrid")

    def test_each_missing_evidence_gate_denies_before_any_venue_call(self):
        exchange = FakeExchange()
        checks = (
            "authenticated_credentials", "venue_live_eligible", "execution_eligible",
            "active_market", "fresh_orderbook", "sufficient_depth", "sufficient_balance",
            "sufficient_capital", "known_fees", "latency_acceptable", "risk_approved",
            "rate_limit_ok", "venue_healthy", "route_certified",
        )
        cfg = SimpleNamespace(mode="live", cross_live=True)
        with live_environment():
            opportunity = cross_opportunity()
            for missing in checks:
                values = {name: True for name in checks}
                values[missing] = False
                evidence = valid_evidence(**values)
                with self.subTest(missing=missing):
                    gate = ExecutionGate(cfg)
                    with self.assertRaisesRegex(ExecutionAuthorizationError, missing):
                        gate.authorize(
                            "cross", venue_ids=("buy", "sell"), route_id="cross:buy>sell",
                            opportunity_id=opportunity_fingerprint("cross", opportunity),
                            evidence=evidence,
                            orders=(
                                OrderScope(0, "buy", "BTC/USDT", "buy", "limit", 0.5, 100),
                                OrderScope(1, "sell", "BTC/USDT", "sell", "limit", 0.5, 102),
                            ),
                        )
            self.assertEqual(exchange.orders, [])

    def test_unknown_and_below_threshold_profit_evidence_is_denied(self):
        with live_environment():
            gate = ExecutionGate(SimpleNamespace(mode="live", cross_live=True))
            for evidence in (
                valid_evidence(expected_pnl=None),
                valid_evidence(worst_case_pnl=0),
                valid_evidence(expected_pnl=0.005),
                valid_evidence(worst_case_bps=0.1),
                valid_evidence(orderbook_observed_at=None),
            ):
                with self.subTest(evidence=evidence):
                    with self.assertRaises(ExecutionAuthorizationError):
                        gate.authorize(
                            "cross", venue_ids=("buy", "sell"), route_id="cross:buy>sell",
                            opportunity_id="candidate", evidence=evidence,
                            orders=(OrderScope(0, "buy", "BTC/USDT", "buy", "limit", 0.5, 100),),
                        )

    def test_order_is_bound_to_strategy_route_opportunity_and_single_use(self):
        cfg = SimpleNamespace(mode="live", cross_live=True)
        exchange = FakeExchange()
        opportunity = cross_opportunity()
        with live_environment():
            gate = ExecutionGate(cfg)
            permit = cross_permit(gate, opportunity)
            scoped_order = ExecutionOrder(0, "buy", "BTC/USDT", "buy", "limit", 0.5, 100.0)
            with self.assertRaisesRegex(ExecutionAuthorizationError, "scope_mismatch"):
                gate.authorize_order(
                    permit, scoped_order, strategy="triangular",
                    route_id="cross:buy>sell",
                    opportunity_id=opportunity_fingerprint("cross", opportunity),
                )
            with self.assertRaisesRegex(ExecutionAuthorizationError, "order_not_in_permit"):
                gate.authorize_order(
                    permit, replace(scoped_order, quantity=0.6), strategy="cross",
                    route_id="cross:buy>sell",
                    opportunity_id=opportunity_fingerprint("cross", opportunity),
                )
            other = cross_opportunity()
            other.base = 0.4
            with self.assertRaisesRegex(ExecutionAuthorizationError, "scope_mismatch"):
                gate.authorize_order(
                    permit, scoped_order, strategy="cross", route_id="cross:buy>sell",
                    opportunity_id=opportunity_fingerprint("cross", other),
                )

    def test_global_kill_switch_is_rechecked_after_api_start_before_submission(self):
        exchange = FakeExchange()
        cfg = SimpleNamespace(mode="live", cross_live=True)
        opportunity = cross_opportunity()
        with live_environment():
            gate = ExecutionGate(cfg)
            permit = cross_permit(gate, opportunity)
            os.environ["ARBX_LIVE_TRADING_ENABLED"] = "0"
            with self.assertRaisesRegex(
                LegFailure, "ARBX_LIVE_TRADING_ENABLED_not_enabled"
            ):
                asyncio.run(CrossExecutor({"buy": exchange, "sell": exchange}, cfg, gate)
                            .execute(opportunity, permit))
        self.assertEqual(exchange.orders, [])

    def test_cross_executor_uses_exact_scoped_order_permits(self):
        buy, sell = FakeExchange(), FakeExchange()
        cfg = SimpleNamespace(mode="live", cross_live=True)
        opportunity = cross_opportunity()
        with live_environment():
            gate = ExecutionGate(cfg)
            permit = cross_permit(gate, opportunity)
            result = asyncio.run(CrossExecutor({"buy": buy, "sell": sell}, cfg, gate)
                                 .execute(opportunity, permit))
        self.assertTrue(result.ok)
        self.assertEqual(len(buy.orders), 1)
        self.assertEqual(len(sell.orders), 1)
        with live_environment():
            with self.assertRaisesRegex(LegFailure, "execution_permit_order_reused"):
                asyncio.run(CrossExecutor({"buy": buy, "sell": sell}, cfg, gate)
                            .execute(opportunity, permit))

    def test_triangular_executor_rechecks_strategy_switch_before_order(self):
        class TriangularExchange(FakeExchange):
            pass

        exchange = TriangularExchange()
        cfg = SimpleNamespace(mode="live", cross_live=True, triangular_live=True)
        leg = SimpleNamespace(symbol="BTC/USDT", side="buy")
        opportunity = SimpleNamespace(
            name="USDT-BTC-USDT", start=100.0, start_asset="USDT",
            expected_final=101.0, worst_final=100.5, net_bps=100.0,
            worst_bps=50.0, legs=(leg,), path=((100.0, 1.0),), limits=(100.0,),
        )
        with live_environment():
            gate = ExecutionGate(cfg)
            permit = gate.authorize(
                "triangular", venue_ids=("venue",), route_id="triangular:venue",
                opportunity_id=opportunity_fingerprint("triangular", opportunity),
                evidence=valid_evidence(),
                orders=(OrderScope(0, "venue", "BTC/USDT", "buy", "limit", 1.0, 100.0),),
            )
            os.environ["BOT_TRIANGULAR_LIVE"] = "0"
            with self.assertRaisesRegex(LegFailure, "BOT_TRIANGULAR_LIVE"):
                asyncio.run(LiveExecutor(exchange, cfg, gate).execute_tri(opportunity, permit))
        self.assertEqual(exchange.orders, [])

    def test_recovery_is_one_shot_and_scoped_to_same_triangular_attempt(self):
        with live_environment():
            gate = ExecutionGate(SimpleNamespace(mode="live", cross_live=True, triangular_live=True))
            permit = gate.authorize(
                "triangular", venue_ids=("venue",), route_id="triangular:venue",
                opportunity_id="one-opportunity", evidence=valid_evidence(),
                orders=(OrderScope(0, "venue", "BTC/USDT", "buy", "limit", 1, 100),),
            )
            with self.assertRaisesRegex(ExecutionAuthorizationError, "emergency_recovery_not_authorized"):
                gate.authorize_recovery(
                    permit, ExecutionOrder(0, "venue", "BTC/USDT", "sell", "market", 1, None)
                )
            main_order = ExecutionOrder(0, "venue", "BTC/USDT", "buy", "limit", 1, 100)
            gate.authorize_order(
                permit, main_order, strategy="triangular", route_id="triangular:venue",
                opportunity_id="one-opportunity",
            )
            gate.note_order_attempt(permit, main_order)
            recovery = ExecutionOrder(0, "venue", "BTC/USDT", "sell", "market", 0.9, None)
            emergency = gate.authorize_recovery(permit, recovery)
            gate.authorize_recovery_order(permit, emergency)
            with self.assertRaises(ExecutionAuthorizationError):
                gate.authorize_recovery_order(permit, emergency)

    def test_recovery_rechecks_kill_switch_at_submit_boundary(self):
        with live_environment():
            gate = ExecutionGate(SimpleNamespace(mode="live", cross_live=True, triangular_live=True))
            permit = gate.authorize(
                "triangular", venue_ids=("venue",), route_id="triangular:venue",
                opportunity_id="one-opportunity", evidence=valid_evidence(),
                orders=(OrderScope(0, "venue", "BTC/USDT", "buy", "limit", 1, 100),),
            )
            main_order = ExecutionOrder(0, "venue", "BTC/USDT", "buy", "limit", 1, 100)
            gate.authorize_order(permit, main_order, strategy="triangular",
                                 route_id="triangular:venue", opportunity_id="one-opportunity")
            gate.note_order_attempt(permit, main_order)
            recovery = gate.authorize_recovery(
                permit, ExecutionOrder(0, "venue", "BTC/USDT", "sell", "market", 0.9, None)
            )
            os.environ["ARBX_LIVE_TRADING_ENABLED"] = "0"
            with self.assertRaisesRegex(ExecutionAuthorizationError, "ARBX_LIVE_TRADING_ENABLED"):
                gate.authorize_recovery_order(permit, recovery)

    def test_hybrid_adapter_cannot_submit_without_an_injected_central_gate_and_permit(self):
        adapter = CCXTProAdapter("binance")
        adapter.ex = SimpleNamespace(create_order=AsyncMock())
        request = OrderRequest("BTC/USDT", "buy", "limit", 0.01, 100.0)
        with self.assertRaisesRegex(AdapterError, "no central execution gate"):
            asyncio.run(adapter.create_order(request))
        adapter.execution_gate = ExecutionGate(SimpleNamespace(mode="live", cross_live=True))
        with self.assertRaisesRegex(AdapterError, "typed central permit"):
            asyncio.run(adapter.create_order(request))
        adapter.ex.create_order.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
