import asyncio
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))
from arbx.config import Config, ExchangeCfg
from arbx.execute import CrossExecutor, LegFailure
from arbx.hub import Hub
from arbx.market import Book
from arbx.strategy import CrossOpp
from arbx.strategy import cross_candidate_sizes


class CrossRankingTests(unittest.TestCase):
    def test_candidate_sizes_stop_at_depth_and_include_price_breakpoints(self):
        buy_book = SimpleNamespace(asks=[[100.0, 1.0], [101.0, 2.0]])
        sell_book = SimpleNamespace(bids=[[102.0, 0.5], [101.0, 0.5]])

        self.assertEqual(cross_candidate_sizes(buy_book, sell_book, 1000), [50.0, 100.0])

    def test_live_budget_scales_to_fresh_quote_and_base_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Config(
                mode="live",
                exchanges=[ExchangeCfg(id="buy"), ExchangeCfg(id="sell")],
                trade_size_usd=25,
                journal_path=pathlib.Path(directory) / "journal.csv",
            )
            hub = Hub(cfg)
            buy_worker = SimpleNamespace(
                free={"USDT": 12.0},
                fee_of=lambda symbol: 0.01,
                ex=SimpleNamespace(markets={"BTC/USDT": {"quote": "USDT", "base": "BTC"}}),
            )
            sell_worker = SimpleNamespace(free={"BTC": 0.2})
            book = SimpleNamespace(asks=[[100.0, 1.0]])
            try:
                size, quote, base = hub._cross_budget_usd(buy_worker, sell_worker, "BTC/USDT", book)
            finally:
                hub.journal_store.close()

        self.assertAlmostEqual(size, 12.0 / 1.012)
        self.assertEqual(quote, 12.0)
        self.assertEqual(base, 0.2)

    def test_global_cross_scan_selects_largest_modeled_floor(self):
        async def scenario(path):
            cfg = Config(
                mode="paper",
                exchanges=[ExchangeCfg(id="venue_a"), ExchangeCfg(id="venue_b"), ExchangeCfg(id="venue_c")],
                trade_size_usd=25,
                min_net_bps=1,
                min_worst_bps=0.1,
                min_profit_usd=0.01,
                limit_tol_bps=0,
                rebalance_haircut_bps=0,
                journal_path=path,
            )
            hub = Hub(cfg)
            now = asyncio.get_running_loop().time()
            # Book.recv is monotonic time; asyncio loop time shares that clock.
            prices = {
                "venue_a": {"BTC/USDT": (99.0, 100.0), "ETH/USDT": (9.0, 10.0)},
                "venue_b": {"BTC/USDT": (104.0, 105.0), "ETH/USDT": (11.0, 12.0)},
                "venue_c": {"BTC/USDT": (103.0, 104.0), "ETH/USDT": (13.0, 14.0)},
            }
            workers = []
            for venue, symbols in prices.items():
                markets = {}
                books = {}
                for symbol, (bid, ask) in symbols.items():
                    base, quote = symbol.split("/")
                    markets[symbol] = {"base": base, "quote": quote}
                    books[symbol] = Book([[bid, 10.0]], [[ask, 10.0]], now)
                worker = SimpleNamespace(
                    id=venue,
                    ex=SimpleNamespace(markets=markets),
                    md=SimpleNamespace(books=books),
                    free={},
                    fee_of=lambda symbol: 0.0,
                    round_price=lambda symbol, price: price,
                    mlimits={},
                    lat=SimpleNamespace(ok=lambda: True),
                    lock=asyncio.Lock(),
                )
                workers.append(worker)
            hub.workers = workers
            hub.cross_syms = {"BTC/USDT", "ETH/USDT"}
            with patch.object(hub, "_fire_cross", new=AsyncMock()) as fire:
                await hub.scan_cross(workers[0], {"BTC/USDT", "ETH/USDT"})
                await hub.scan_cross(workers[0], {"BTC/USDT", "ETH/USDT"})
                self.assertEqual(fire.await_count, 1)
                selected = fire.await_args.args[0]
                self.assertEqual(selected.symbol, "ETH/USDT")
                self.assertEqual(selected.sell_ex, "venue_c")
            hub.journal_store.close()

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(pathlib.Path(directory) / "journal.csv"))

    def test_direction_ties_prefer_higher_available_capital_utilization(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Config(
                mode="live",
                exchanges=[ExchangeCfg(id="buy"), ExchangeCfg(id="sell")],
                trade_size_usd=25,
                journal_path=pathlib.Path(directory) / "journal.csv",
            )
            hub = Hub(cfg)
            buy_worker = SimpleNamespace(
                free={"USDT": 100.0},
                fee_of=lambda symbol: 0.0,
                ex=SimpleNamespace(markets={"BTC/USDT": {"quote": "USDT", "base": "BTC"}}),
                mlimits={},
                lat=SimpleNamespace(ok=lambda: True),
            )
            sell_worker = SimpleNamespace(free={"BTC": 1.0}, mlimits={}, lat=buy_worker.lat)

            def opportunity(*args):
                size = args[5]
                return SimpleNamespace(
                    cost=size, worst_usd=1.0, expected_usd=2.0,
                    worst_bps=200.0 if size == 10.0 else 100.0,
                )

            try:
                with patch("arbx.hub.cross_candidate_sizes", return_value=[10.0, 20.0]), \
                     patch("arbx.hub.evaluate_cross", side_effect=opportunity), \
                     patch.object(hub.gate, "check_cross", return_value=SimpleNamespace(ok=True)):
                    candidate = hub._best_cross_for_direction(
                        "BTC/USDT", buy_worker, sell_worker,
                        SimpleNamespace(asks=[[100.0, 1.0]]),
                        SimpleNamespace(bids=[[101.0, 1.0]]),
                        1.0,
                    )
            finally:
                hub.journal_store.close()

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate[1].cost, 20.0)
        self.assertEqual(candidate[5], 0.2)

    def test_unmatched_cross_venue_fills_halt_instead_of_counting_as_profit(self):
        class FakeExchange:
            def __init__(self, filled):
                self.filled = filled

            def amount_to_precision(self, symbol, amount):
                return str(amount)

            async def create_order(self, symbol, order_type, side, amount, price, params):
                return {"id": side, "filled": self.filled, "status": "closed",
                        "cost": self.filled * price, "fees": []}

        opportunity = CrossOpp(
            symbol="BTC/USDT", buy_ex="buy", sell_ex="sell", base=1.0, limit_buy=100.0,
            limit_sell=102.0, cost=100.0, expected_usd=2.0, worst_usd=1.0,
            net_bps=200.0, worst_bps=100.0, age_ms=1.0, base_ccy="BTC", quote_ccy="USDT",
        )
        executor = CrossExecutor({"buy": FakeExchange(1.0), "sell": FakeExchange(0.99)})

        with self.assertRaisesRegex(LegFailure, "inventory imbalance"):
            asyncio.run(executor.execute(opportunity))


if __name__ == "__main__":
    unittest.main()
