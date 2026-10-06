from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from arbx.util import STABLES

CCXT_ADAPTERS = {"gateio": "gate"}
MAX_EXCHANGES = 20
LIVE_STARTER_CAPITAL_USD = 3.0


@dataclass
class ExchangeCfg:
    id: str
    venue_id: str | None = None
    api_key: str = ""
    secret: str = ""
    password: str = ""
    max_symbols: int = 120
    default_taker_bps: float = 10.0
    require_private_stream: bool = False
    auth_mode: str = "hmac"


@dataclass
class Config:
    mode: str = "paper"
    exchanges: list[ExchangeCfg] = field(default_factory=list)
    start_assets: tuple[str, ...] = ("USDT", "USDC")
    start_capital_usd: float = LIVE_STARTER_CAPITAL_USD
    starter_capital_usd: float = LIVE_STARTER_CAPITAL_USD
    trade_size_usd: float = LIVE_STARTER_CAPITAL_USD
    target_profit_usd: float | None = None
    target_equity_usd: float | None = None

    # Live strategy policy: $3 starter, reinvest realized profits, never average
    # down a losing trade. The word DCA here means staged capital deployment
    # from realized profits; it does not mean adding to a losing position.
    strategy_mode: str = "profit_dca"
    compound_profits: bool = True
    halt_on_realized_loss: bool = True

    # ---- Modeled profit-floor gate ------------------------------------------
    min_net_bps: float = 3.0
    min_worst_bps: float = 0.5
    limit_tol_bps: float = 1.0
    min_profit_usd: float = 0.01
    verify_slack_bps: float = 1.0

    # ---- Latency / speed ----------------------------------------------------
    max_book_age_ms: float = 250.0
    max_rtt_ms: float = 80.0
    pause_rtt_ms: float = 150.0
    depth: int = 10

    # ---- Risk ---------------------------------------------------------------
    # Kept only for backwards-compatible control-plane payloads. Live trading
    # does not use a fixed session-loss ceiling; a realized loss is instead a
    # circuit-breaker event under halt_on_realized_loss.
    max_loss_usd: float = 0.0
    max_consecutive_failures: int = 3
    max_trades_per_min: int = 30
    cooldown_s: float = 0.2

    # ---- Fees / vehicles ----------------------------------------------------
    fee_discount_pct: float = 0.0

    # ---- Cross-exchange (pre-funded inventory on both sides; no on-chain hop in the loop) ----
    cross_enabled: bool = True
    cross_live: bool = False
    cross_top_n: int = 30
    rebalance_haircut_bps: float = 2.0

    paper_penalty_bps: float = 1.0
    journal_path: Path = Path("trade_journal.csv")

    @classmethod
    def from_env(cls) -> "Config":
        def f(n, d):
            v = os.getenv(n)
            return float(v) if v else d

        def b(n, d):
            v = os.getenv(n)
            return v.lower() in ("1", "true", "yes") if v else d

        ids = [x.strip().lower() for x in os.getenv("BOT_EXCHANGES", "binance,bybit").split(",") if x.strip()]
        mx = int(f("BOT_MAX_SYMBOLS", 120))
        xs = [ExchangeCfg(id=CCXT_ADAPTERS.get(i, i), venue_id=i,
                          api_key=os.getenv(f"BOT_{i.upper()}_KEY", ""),
                          secret=os.getenv(f"BOT_{i.upper()}_SECRET", ""),
                          password=os.getenv(f"BOT_{i.upper()}_PASSWORD", ""),
                          auth_mode=os.getenv(f"BOT_{i.upper()}_AUTH_MODE", "hmac").lower(),
                          max_symbols=mx, default_taker_bps=f("BOT_DEFAULT_TAKER_BPS", 10.0)) for i in ids]
        mode = os.getenv("BOT_MODE", "paper").lower()
        tgt = os.getenv("BOT_TARGET_EQUITY_USD")
        profit_target = os.getenv("BOT_TARGET_PROFIT_USD")
        starter = LIVE_STARTER_CAPITAL_USD if mode == "live" else f("BOT_START_CAPITAL_USD", LIVE_STARTER_CAPITAL_USD)
        trade_size = starter if mode == "live" else f("BOT_TRADE_SIZE_USD", starter)
        return cls(
            mode=mode, exchanges=xs,
            start_capital_usd=starter, starter_capital_usd=starter, trade_size_usd=trade_size,
            target_profit_usd=float(profit_target) if profit_target else None,
            target_equity_usd=float(tgt) if tgt else None,
            strategy_mode=os.getenv("BOT_STRATEGY_MODE", "profit_dca"),
            compound_profits=b("BOT_COMPOUND_PROFITS", True),
            halt_on_realized_loss=b("BOT_HALT_ON_REALIZED_LOSS", True),
            min_net_bps=f("BOT_MIN_NET_BPS", 3.0), min_worst_bps=f("BOT_MIN_WORST_BPS", 0.5),
            limit_tol_bps=f("BOT_LIMIT_TOL_BPS", 1.0), max_rtt_ms=f("BOT_MAX_RTT_MS", 80.0),
            fee_discount_pct=f("BOT_FEE_DISCOUNT_PCT", 0.0),
            cross_enabled=b("BOT_CROSS", True), cross_live=b("BOT_CROSS_LIVE", False),
            journal_path=Path(os.getenv("BOT_JOURNAL_PATH", "trade_journal.csv")),
        )

    def validate(self) -> None:
        if self.mode not in ("paper", "live"):
            raise ValueError("BOT_MODE must be 'paper' or 'live'")
        if self.strategy_mode != "profit_dca":
            raise ValueError("live strategy is fixed to profit_dca") if self.mode == "live" else None
        if self.mode == "live":
            # Normalize every live entry path, including the authenticated web
            # control API, to the single $3 starter-capital condition.
            self.start_capital_usd = LIVE_STARTER_CAPITAL_USD
            self.starter_capital_usd = LIVE_STARTER_CAPITAL_USD
            self.trade_size_usd = LIVE_STARTER_CAPITAL_USD
            self.target_profit_usd = None
            self.max_loss_usd = 0.0
            self.compound_profits = True
            self.halt_on_realized_loss = True
        if not self.exchanges:
            raise ValueError("no exchanges configured (BOT_EXCHANGES)")
        venue_ids = [x.venue_id or x.id for x in self.exchanges]
        if len(self.exchanges) > MAX_EXCHANGES or len(set(venue_ids)) != len(venue_ids):
            raise ValueError(f"configure between one and {MAX_EXCHANGES} unique exchange venues")
        if self.target_profit_usd is not None and self.target_profit_usd <= 0:
            raise ValueError("BOT_TARGET_PROFIT_USD must be greater than zero")
        if self.starter_capital_usd <= 0:
            raise ValueError("BOT_START_CAPITAL_USD must be greater than zero")
        if self.mode == "live" and self.starter_capital_usd != LIVE_STARTER_CAPITAL_USD:
            raise ValueError("live mode is fixed to $3 starter capital")
        if self.trade_size_usd <= 0:
            raise ValueError("BOT_TRADE_SIZE_USD must be greater than zero")
        if self.mode == "live" and self.trade_size_usd > LIVE_STARTER_CAPITAL_USD:
            raise ValueError("live trade size cannot exceed the $3 starter capital allocation")
        bad = [a for a in self.start_assets if a not in STABLES]
        if bad:
            raise ValueError(f"start assets must be stablecoins, got {bad}")
        if self.mode == "live":
            if self.strategy_mode not in ("profit_dca", "hybrid"):
                raise ValueError("live strategy must be profit_dca or hybrid")
            if len(self.exchanges) < 2 or not self.cross_enabled or not self.cross_live:
                raise ValueError("live mode requires at least two venues and explicit BOT_CROSS_LIVE=1")
            for x in self.exchanges:
                if not (x.api_key and x.secret):
                    venue_id = x.venue_id or x.id
                    raise ValueError(f"live mode needs BOT_{venue_id.upper()}_KEY and BOT_{venue_id.upper()}_SECRET")
