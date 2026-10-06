from __future__ import annotations
import os
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
    async def watch_depth(self,symbol,limit=50):
        return normalize_depth(self.venue_id,symbol,await self.ex.watch_order_book(symbol,limit),limit=limit)
    async def fetch_balance(self): return await self.ex.fetch_balance()
    async def verify_private_stream(self):
        if self.ex.has.get("watchBalance") is not True: return False
        b=await self.ex.watch_balance(); return isinstance(b,dict) and all(k in b for k in ("free","used","total"))
    async def verify_permissions(self): return {"liveEligible":False,"reason":"delegate to arbx.permissions"}
    async def verify_execution(self,symbol):
        m=self.ex.markets.get(symbol) or {}
        return {"market":bool(m),"spot":bool(m.get("spot")),"createOrder":self.ex.has.get("createOrder") is True,
                "fetchOrder":self.ex.has.get("fetchOrder") is True,"watchOrderBook":self.ex.has.get("watchOrderBook") is True}

class ExternalProcessAdapter(BaseAdapter):
    """Optional Freqtrade/Hummingbot strategy bridge; never grants live eligibility."""
    def __init__(self,venue_id,command_env):
        super().__init__(venue_id); self.command_env=command_env; self.command=os.getenv(command_env,"")
    async def connect(self):
        if not self.command: raise AdapterError(f"{self.command_env} is not configured")
    async def close(self): pass
    async def verify_private_stream(self): return False
    async def verify_permissions(self): return {"liveEligible":False,"reason":"external bridge requires canonical venue evidence"}
    async def verify_execution(self,symbol): return {"executionEligible":False,"reason":"external bridge is strategy-only"}

def optional_strategy_adapters():
    return {"freqtrade":lambda venue:ExternalProcessAdapter(venue,"NU_ARB_FREQTRADE_CMD"),
            "hummingbot":lambda venue:ExternalProcessAdapter(venue,"NU_ARB_HUMMINGBOT_CMD")}
