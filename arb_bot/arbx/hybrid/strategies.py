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
