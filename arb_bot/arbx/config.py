from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from arbx.util import STABLES


@dataclass
class ExchangeCfg:
    id: str                       # ccxt.pro exchange id, e.g. "binance"
    api_key: str = ""
    secret: str = ""
    password: str = ""            # only some exchanges (okx, kucoin, bitget)
    max_symbols: int = 120        # order-book streams to keep open on this exchange
    default_taker_bps: float = 10.0
    require_private_stream: bool = False
    auth_mode: str = "hmac"


@dataclass
class Config:
    mode: str = "paper"                                   # "paper" | "live"
    exchanges: list[ExchangeCfg] = field(default_factory=list)
    start_assets: tuple[str, ...] = ("USDT", "USDC")      # cycles start/end here (must be stablecoins)
    start_capital_usd: float = 100.0                      # paper only
    trade_size_usd: float = 25.0
    target_profit_usd: float | None = None       # session-wide realized net PnL target; stops new orders at attainment
    target_equity_usd: float | None = None                # halt (never withdraw) when reached

    # ---- Profit/No-Loss gate ------------------------------------------------
    min_net_bps: float = 3.0          # expected edge after fees (VWAP over depth)
    min_worst_bps: float = 0.5        # edge if EVERY leg fills at its limit price (hard guarantee)
    limit_tol_bps: float = 1.0        # IOC limit = marginal price +/- this tolerance
    min_profit_usd: float = 0.01      # guaranteed profit floor per trade
    verify_slack_bps: float = 1.0     # post-trade: realized may miss guarantee by at most this

    # ---- Latency / speed ----------------------------------------------------
    max_book_age_ms: float = 250.0    # local receive-time freshness
    max_rtt_ms: float = 80.0          # live refuses to start if median REST RTT is above this
    pause_rtt_ms: float = 150.0       # trading pauses while rolling p95 RTT is above this
    depth: int = 10

    # ---- Risk ---------------------------------------------------------------
    max_loss_usd: float = 5.0
    max_consecutive_failures: int = 3
    max_trades_per_min: int = 30
    cooldown_s: float = 0.2

    # ---- Fees / vehicles ----------------------------------------------------
    fee_discount_pct: float = 0.0     # e.g. 25 if you pay fees in the exchange token (BNB on Binance)

    # ---- Cross-exchange (pre-funded inventory on both sides; no on-chain hop in the loop) ----
    cross_enabled: bool = True
    cross_live: bool = False          # extra explicit opt-in for live cross-exchange orders
    cross_top_n: int = 30
    rebalance_haircut_bps: float = 2.0  # amortized network/rebalancing cost charged against every cross trade

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
        xs = [ExchangeCfg(id=i, api_key=os.getenv(f"BOT_{i.upper()}_KEY", ""),
                          secret=os.getenv(f"BOT_{i.upper()}_SECRET", ""),
                          password=os.getenv(f"BOT_{i.upper()}_PASSWORD", ""),
                          auth_mode=os.getenv(f"BOT_{i.upper()}_AUTH_MODE", "hmac").lower(),
                          max_symbols=mx, default_taker_bps=f("BOT_DEFAULT_TAKER_BPS", 10.0)) for i in ids]
        tgt = os.getenv("BOT_TARGET_EQUITY_USD")
        profit_target = os.getenv("BOT_TARGET_PROFIT_USD")
        return cls(
            mode=os.getenv("BOT_MODE", "paper").lower(), exchanges=xs,
            start_capital_usd=f("BOT_START_CAPITAL_USD", 100.0), trade_size_usd=f("BOT_TRADE_SIZE_USD", 25.0),
            target_profit_usd=float(profit_target) if profit_target else None,
            target_equity_usd=float(tgt) if tgt else None,
            min_net_bps=f("BOT_MIN_NET_BPS", 3.0), min_worst_bps=f("BOT_MIN_WORST_BPS", 0.5),
            limit_tol_bps=f("BOT_LIMIT_TOL_BPS", 1.0), max_rtt_ms=f("BOT_MAX_RTT_MS", 80.0),
            max_loss_usd=f("BOT_MAX_LOSS_USD", 5.0), fee_discount_pct=f("BOT_FEE_DISCOUNT_PCT", 0.0),
            cross_enabled=b("BOT_CROSS", True), cross_live=b("BOT_CROSS_LIVE", False),
            journal_path=Path(os.getenv("BOT_JOURNAL_PATH", "trade_journal.csv")),
        )

    def validate(self) -> None:
        if self.mode not in ("paper", "live"):
            raise ValueError("BOT_MODE must be 'paper' or 'live'")
        if not self.exchanges:
            raise ValueError("no exchanges configured (BOT_EXCHANGES)")
        if self.target_profit_usd is not None and self.target_profit_usd <= 0:
            raise ValueError("BOT_TARGET_PROFIT_USD must be greater than zero")
        bad = [a for a in self.start_assets if a not in STABLES]
        if bad:
            raise ValueError(f"start assets must be stablecoins, got {bad}")
        if self.mode == "live":
            for x in self.exchanges:
                if not (x.api_key and x.secret):
                    raise ValueError(f"live mode needs BOT_{x.id.upper()}_KEY and BOT_{x.id.upper()}_SECRET")

