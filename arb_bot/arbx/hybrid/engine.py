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
        a=self.adapters[venue_id]; reasons=[]
        rest=public=private=balance=perm=execution=depth=False
        detail={"venue":venue_id,"adapter":a.adapter_name,"symbol":symbol}
        try:
            await a.connect()
            try:
                detail["rest"]=await a.verify_rest(); rest=bool(detail["rest"].get("ok"))
            except Exception as exc: detail["rest"]={"ok":False,"error":type(exc).__name__+": "+str(exc)}
            try:
                detail["publicWS"]=await a.verify_public_stream(symbol,max(50,self.cfg.depth)); public=bool(detail["publicWS"].get("ok"))
            except Exception as exc: detail["publicWS"]={"ok":False,"error":type(exc).__name__+": "+str(exc)}
            try:
                bal=await a.fetch_balance(); balance=isinstance(bal,dict) and all(isinstance(bal.get(k),dict) for k in ("free","used","total")); detail["balance"]={"ok":balance}
            except Exception as exc: detail["balance"]={"ok":False,"error":type(exc).__name__+": "+str(exc)}
            if self.cfg.mode=="live":
                try: detail["privateWS"]=await a.verify_private_stream(); private=bool(detail["privateWS"].get("ok"))
                except Exception as exc: detail["privateWS"]={"ok":False,"error":type(exc).__name__+": "+str(exc)}
            else: detail["privateWS"]={"ok":True,"state":"not_required_in_paper"}; private=True
            try: detail["permissions"]=await a.verify_permissions(); perm=bool(detail["permissions"].get("liveEligible"))
            except Exception as exc: detail["permissions"]={"liveEligible":False,"error":type(exc).__name__+": "+str(exc)}
            try: detail["execution"]=await a.verify_execution(symbol); execution=all(detail["execution"].get(k) for k in ("market","spot","createOrder","fetchOrder","watchOrderBook","iocLimit"))
            except Exception as exc: detail["execution"]={"ok":False,"error":type(exc).__name__+": "+str(exc)}
            if public:
                try:
                    book=await a.watch_depth(symbol,max(50,self.cfg.depth)); d=validate_depth(book,notional_usd or self.cfg.trade_size_usd,self.cfg.max_book_age_ms); depth=d.ok
                    detail["depth"]={"ok":depth,"reason":d.reason,"depthUsd":d.depth_usd,"bestBid":d.top_bid,"bestAsk":d.top_ask,"spreadBps":d.spread_bps,"levels":d.levels}
                except Exception as exc: detail["depth"]={"ok":False,"error":type(exc).__name__+": "+str(exc)}
            else: detail["depth"]={"ok":False,"reason":"public_ws_unverified"}
            checks=(("rest",rest),("public_ws",public),("private_ws",private),("balance",balance),("permissions",perm),("execution",execution),("depth",depth))
            reasons=[name for name,ok in checks if not ok]
            live=all(ok for _,ok in checks) if self.cfg.mode=="live" else all(ok for name,ok in checks if name not in ("private_ws","permissions"))
            ev=VenueEvidence(venue_id,a.adapter_name,rest,public,private,balance,perm,execution,depth,live,tuple(reasons),detail)
        except Exception as exc:
            ev=VenueEvidence(venue_id,a.adapter_name,False,False,False,False,False,False,False,False,(type(exc).__name__,str(exc)),detail)
        self.evidence[venue_id]=ev
        return ev
        
    def eligible_venues(self): return [v for v,e in self.evidence.items() if e.live_eligible]
    def strategy_catalog(self): return strategy_catalog()
