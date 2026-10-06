from __future__ import annotations
from dataclasses import dataclass
from .contracts import VenueEvidence
from .registry import VENUE_CATALOG
from .adapters import CCXTProAdapter
from .depth import validate_depth
from .strategies import strategy_catalog

@dataclass
class HybridEngine:
    cfg:object
    adapters:dict
    evidence:dict

    @classmethod
    def create(cls,cfg):
        adapters={}
        for spec in VENUE_CATALOG:
            match=next((x for x in cfg.exchanges if (x.venue_id or x.id)==spec.id),None)
            if match:
                c={}
                if match.api_key:c["apiKey"]=match.api_key
                if match.secret:c["secret" if match.auth_mode=="hmac" else "privateKey"]=match.secret
                if match.password:c["password"]=match.password
                adapters[spec.id]=CCXTProAdapter(spec.id,spec.ccxt_id,c)
        return cls(cfg,adapters,{})
    async def validate_venue(self,venue_id,symbol="BTC/USDT",notional_usd=None):
        a=self.adapters[venue_id]; reasons=[]; rest=public=private=balance=perm=execution=depth=False
        try:
            await a.connect(); rest=True
            book=await a.watch_depth(symbol,max(50,self.cfg.depth)); public=True
            private=await a.verify_private_stream() if self.cfg.mode=="live" else True
            bal=await a.fetch_balance(); balance=isinstance(bal,dict)
            p=await a.verify_permissions(); perm=bool(p.get("liveEligible"))
            e=await a.verify_execution(symbol); execution=all(e.get(k) for k in ("market","spot","createOrder","fetchOrder","watchOrderBook"))
            d=validate_depth(book,notional_usd or self.cfg.trade_size_usd,self.cfg.max_book_age_ms); depth=d.ok
            for n,ok in (("rest",rest),("public_ws",public),("private_ws",private),("balance",balance),("permissions",perm),("execution",execution),("depth",depth)):
                if not ok: reasons.append(n)
            live=all((rest,public,private,balance,perm,execution,depth)) if self.cfg.mode=="live" else all((rest,public,execution,depth))
            ev=VenueEvidence(venue_id,"ccxt_pro",rest,public,private,balance,perm,execution,depth,live,tuple(reasons),
                {"depth":{"bestBid":book.best_bid,"bestAsk":book.best_ask,"levels":min(len(book.bids),len(book.asks))},"execution":e,"permissions":p})
            self.evidence[venue_id]=ev; return ev
        except Exception as exc:
            ev=VenueEvidence(venue_id,"ccxt_pro",rest,public,private,balance,perm,execution,depth,False,(type(exc).__name__,str(exc)),{})
            self.evidence[venue_id]=ev; return ev
        finally: await a.close()
    def eligible_venues(self): return [v for v,e in self.evidence.items() if e.live_eligible]
    def strategy_catalog(self): return strategy_catalog()
