const COIN_IDS = Object.freeze({BTC:"bitcoin",ETH:"ethereum",SOL:"solana",XRP:"ripple",BNB:"binancecoin",DOGE:"dogecoin",ADA:"cardano",TRX:"tron",LTC:"litecoin",BCH:"bitcoin-cash",LINK:"chainlink",DOT:"polkadot",AVAX:"avalanche-2",SHIB:"shiba-inu",TON:"the-open-network"});
const STABLE_QUOTES = new Set(["USDT","USDC","DAI"]);
const MAX_REFERENCE_AGE_MS = 180000;

export class CoinGeckoReferenceProvider {
  constructor({fetchImpl=globalThis.fetch, now=()=>Date.now(), cacheTtlMs=45000, timeoutMs=3000}={}) {
    this.fetchImpl=fetchImpl; this.now=now; this.cacheTtlMs=cacheTtlMs; this.timeoutMs=timeoutMs; this.cache=null; this.expiresAt=0;
  }
  async getPrices(symbols) {
    const pairs=[...new Set(symbols.filter(s=>typeof s==="string"&&s.includes("/")&&COIN_IDS[s.split("/",1)[0]]&&STABLE_QUOTES.has(s.split("/",2)[1])))].sort();
    if(!pairs.length) return {status:"unavailable",provider:"CoinGecko",prices:{},ageSeconds:null};
    if(!this.cache||this.now()>=this.expiresAt) {
      try {
        const ids=[...new Set(Object.values(COIN_IDS))].sort().join(",");
        const url="https://api.coingecko.com/api/v3/simple/price?"+new URLSearchParams({ids,vs_currencies:"usd",include_last_updated_at:"true"});
        const response=await this.fetchImpl(url,{headers:{accept:"application/json","user-agent":"NU-ARB-readonly-reference/1.0"},signal:AbortSignal.timeout(this.timeoutMs)});
        if(!response?.ok) throw new Error("CoinGecko reference request failed");
        const payload=await response.json(); const fetched={};
        for(const [symbol,id] of Object.entries(COIN_IDS)) {
          const row=payload?.[id]; const price=Number(row?.usd); const updatedAt=Number(row?.last_updated_at)*1000; const age=this.now()-updatedAt;
          if(Number.isFinite(price)&&price>0&&Number.isFinite(updatedAt)&&updatedAt>0&&Number.isFinite(age)&&age>=-30000&&age<=MAX_REFERENCE_AGE_MS) fetched[symbol]={priceUsd:price,updatedAt};
        }
        this.cache=fetched; this.expiresAt=this.now()+this.cacheTtlMs;
      } catch { if(!this.cache) return {status:"unavailable",provider:"CoinGecko",prices:{},ageSeconds:null}; }
    }
    const prices={}; const ages=[];
    for(const pair of pairs) {
      const entry=this.cache?.[pair.split("/",1)[0]]; if(!entry) continue;
      const age=(this.now()-entry.updatedAt)/1000;
      if(!Number.isFinite(age)||age< -30||age>180||!Number.isFinite(entry.priceUsd)||entry.priceUsd<=0) continue;
      prices[pair]=entry.priceUsd; ages.push(Math.max(0,age));
    }
    return {status:Object.keys(prices).length?"available":"unavailable",provider:"CoinGecko",prices,ageSeconds:ages.length?Math.round(Math.max(...ages)*10)/10:null};
  }
}
