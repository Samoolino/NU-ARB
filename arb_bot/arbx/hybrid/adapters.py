from __future__ import annotations
import os
import time
from abc import ABC, abstractmethod
from .depth import normalize_depth

class AdapterError(RuntimeError): pass

class BaseAdapter(ABC):
    adapter_name="base"
    def __init__(self,venue_id): self.venue_id=venue_id
    @abstractmethod
    async def connect(self): ...
    @abstractmethod
    async def close(self): ...

class CCXTProAdapter(BaseAdapter):
    """Canonical venue transport. Every live claim is backed by fresh REST/WS evidence."""
    adapter_name="ccxt_pro"
    def __init__(self,venue_id,ccxt_id=None,credentials=None):
        super().__init__(venue_id); self.ccxt_id=ccxt_id or venue_id; self.credentials=credentials or {}; self.ex=None
    async def connect(self):
        import ccxt.pro as ccxtpro
        cls=getattr(ccxtpro,self.ccxt_id,None)
        if cls is None: raise AdapterError(f"CCXT Pro adapter unavailable: {self.ccxt_id}")
        p={"enableRateLimit":True,"options":{"defaultType":"spot"}}
        p.update({k:v for k,v in self.credentials.items() if v}); self.ex=cls(p); await self.ex.load_markets()
    async def close(self):
        if self.ex is not None: await self.ex.close()
    async def verify_rest(self):
        started=time.perf_counter()
        if self.ex.has.get("fetchTime") is True:
            server_ms=await self.ex.fetch_time()
        else:
            await self.ex.fetch_markets()
            server_ms=None
        return {"ok":True,"rttMs":round((time.perf_counter()-started)*1000,1),"serverTimeMs":server_ms,
                "marketsLoaded":bool(self.ex.markets)}
    async def verify_public_stream(self,symbol,limit=50):
        started=time.perf_counter()
        if self.ex.has.get("watchOrderBook") is not True:
            return {"ok":False,"reason":"watchOrderBook unavailable"}
        book=await self.ex.watch_order_book(symbol,limit)
        ok=bool(book.get("bids") and book.get("asks"))
        return {"ok":ok,"rttMs":round((time.perf_counter()-started)*1000,1),"sequence":book.get("nonce"),
                "exchangeTimestampMs":book.get("timestamp"),"book":book}
    async def watch_depth(self,symbol,limit=50):
        result=await self.verify_public_stream(symbol,limit)
        if not result.get("ok"): raise AdapterError(result.get("reason","public order book unavailable"))
        return normalize_depth(self.venue_id,symbol,result["book"],limit=limit)
    async def fetch_balance(self): return await self.ex.fetch_balance()
    async def verify_private_stream(self):
        if self.ex.has.get("watchBalance") is not True:
            return {"ok":False,"reason":"watchBalance unavailable"}
        b=await self.ex.watch_balance()
        ok=isinstance(b,dict) and all(k in b for k in ("free","used","total"))
        return {"ok":ok,"balanceKeys":sorted(k for k in ("free","used","total") if isinstance(b,dict) and k in b)}
    async def verify_permissions(self):
        from arbx.permissions import inspect_permissions
        return await inspect_permissions(self.venue_id,self.ex)
    async def verify_execution(self,symbol):
        m=self.ex.markets.get(symbol) or {}
        feature_value=getattr(self.ex,"feature_value",None)
        tif=None
        if callable(feature_value):
            try: tif=feature_value(symbol,"createOrder","timeInForce")
            except Exception: pass
        return {"market":bool(m),"spot":bool(m.get("spot")),"contract":bool(m.get("contract")),
                "createOrder":self.ex.has.get("createOrder") is True,"fetchOrder":self.ex.has.get("fetchOrder") is True,
                "cancelOrder":self.ex.has.get("cancelOrder") is True,"watchOrderBook":self.ex.has.get("watchOrderBook") is True,
                "createMarketOrder":self.ex.has.get("createMarketOrder") is True,
                "iocLimit":isinstance(tif,dict) and tif.get("IOC") is True}

class ExternalProcessAdapter(BaseAdapter):
    """Optional Freqtrade/Hummingbot strategy bridge; never grants live eligibility."""
    def __init__(self,venue_id,command_env):
        super().__init__(venue_id); self.command_env=command_env; self.command=os.getenv(command_env,"")
    async def connect(self):
        if not self.command: raise AdapterError(f"{self.command_env} is not configured")
    async def close(self): pass
    async def verify_private_stream(self): return {"ok":False,"reason":"external strategy bridge"}
    async def verify_permissions(self): return {"liveEligible":False,"reason":"external bridge requires canonical venue evidence"}
    async def verify_execution(self,symbol): return {"executionEligible":False,"reason":"external bridge is strategy-only"}

def optional_strategy_adapters():
    return {"freqtrade":lambda venue:ExternalProcessAdapter(venue,"NU_ARB_FREQTRADE_CMD"),
            "hummingbot":lambda venue:ExternalProcessAdapter(venue,"NU_ARB_HUMMINGBOT_CMD")}
