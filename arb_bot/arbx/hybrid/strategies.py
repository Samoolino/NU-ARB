from __future__ import annotations
from dataclasses import dataclass
from .depth import walk_asks,walk_bids
from .contracts import NormalizedDepth

@dataclass(frozen=True,slots=True)
class Opportunity:
    strategy:str
    route:tuple[str,...]
    symbol:str
    notional_usd:float
    expected_net_usd:float
    worst_net_usd:float
    expected_bps:float
    worst_bps:float
    reason:str=""

def cross_exchange(buy,sell,notional_usd,buy_fee_bps,sell_fee_bps,safety_bps=2.0):
    if buy.symbol!=sell.symbol or buy.best_ask<=0 or sell.best_bid<=0:return None
    got=walk_asks(buy,notional_usd)
    if got is None:return None
    sold=walk_bids(sell,got.quantity)
    if sold is None:return None
    gross=sold.quote_cost-got.quote_cost
    net=gross-got.quote_cost*buy_fee_bps/10000-sold.quote_cost*sell_fee_bps/10000-got.quote_cost*safety_bps/10000
    bps=net/got.quote_cost*10000
    worst=sold.quantity*sell.best_bid*(1-sell_fee_bps/10000)-got.quantity*got.worst_price*(1+buy_fee_bps/10000)-got.quote_cost*safety_bps/10000
    return Opportunity("cross_exchange",(buy.venue,sell.venue),buy.symbol,notional_usd,net,worst,bps,worst/got.quote_cost*10000)

def stablecoin_arbitrage(a,b,notional_usd,fee_bps):
    return cross_exchange(a,b,notional_usd,fee_bps,fee_bps,3.0)

def profit_compounding_capital(starter,realized_profit):
    return max(0.0,starter+max(0.0,realized_profit))

def strategy_catalog():
    return ("dca_profit_compounding","cross_exchange","triangular_intra_exchange","triangular_multi_exchange","stablecoin_arbitrage","spot")


def strategy_execution_catalog():
    """Describe implementation and live-path boundaries; cataloging is not verification."""
    return (
        {
            "id": "cross_exchange",
            "kind": "execution_strategy",
            "implementation": "implemented",
            "executionGateStrategy": "cross",
            "livePath": "implemented_fail_closed",
            "connectionRequirements": (
                "two_distinct_fresh_live_eligible_venues",
                "prefunded_quote_and_base_inventory",
                "private_and_public_streams",
                "certified_ioc_route",
                "BOT_CROSS_LIVE=1",
            ),
        },
        {
            "id": "triangular_intra_exchange",
            "kind": "execution_strategy",
            "implementation": "implemented",
            "executionGateStrategy": "triangular",
            "livePath": "implemented_fail_closed",
            "connectionRequirements": (
                "one_fresh_live_eligible_venue",
                "three_active_spot_markets",
                "prefunded_cycle_inventory",
                "private_and_public_streams",
                "certified_ioc_route",
                "BOT_TRIANGULAR_LIVE=1",
            ),
        },
        {
            "id": "dca_profit_compounding",
            "kind": "capital_policy",
            "implementation": "implemented_policy_not_strategy",
            "executionGateStrategy": None,
            "livePath": "policy_only",
            "connectionRequirements": ("known_realized_pnl", "risk_gate_approval"),
        },
        {
            "id": "stablecoin_arbitrage",
            "kind": "opportunity_calculation",
            "implementation": "helper_only",
            "executionGateStrategy": "cross",
            "livePath": "no_dedicated_route",
            "connectionRequirements": ("cross_exchange_route_must_be_independently_certified",),
        },
        {
            "id": "triangular_multi_exchange",
            "kind": "catalog_label",
            "implementation": "no_distinct_execution_path",
            "executionGateStrategy": None,
            "livePath": "unsupported",
            "connectionRequirements": (),
        },
        {
            "id": "spot",
            "kind": "market_type",
            "implementation": "market_type_not_strategy",
            "executionGateStrategy": None,
            "livePath": "not_applicable",
            "connectionRequirements": (),
        },
    )
