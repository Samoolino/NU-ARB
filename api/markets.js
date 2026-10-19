import ccxt from "ccxt";
import { EXCHANGES, EXCHANGE_BY_ID } from "../lib/exchange-registry.js";
import { normalizeCoinListRows, normalizeCoinMarketRows, normalizeVenueMarketRows, parseUniversePage } from "../lib/market-universe.js";

const COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets";
const MAX_VENUES_PER_REQUEST = 4;
let coinListCache = null;
let coinListCacheUntil = 0;

export default async function handler(request, response) {
  if (request.method !== "GET") {
    response.setHeader("Allow", "GET");
    return response.status(405).json({ error: "Use GET" });
  }
  response.setHeader("Cache-Control", "public, s-maxage=30, stale-while-revalidate=60");
  response.setHeader("X-Content-Type-Options", "nosniff");
  const source = typeof request.query?.source === "string" ? request.query.source : "coingecko";
  if (source === "coingecko") {
    const paging = parseUniversePage(request.query?.page, request.query?.perPage);
    if (!paging) return response.status(400).json({ error: "page must be 1–100 and perPage must be 1–100" });
    try {
      const url = new URL(COINGECKO_MARKETS_URL);
      url.search = new URLSearchParams({ vs_currency: "usd", order: "market_cap_desc", per_page: String(paging.perPage), page: String(paging.page), sparkline: "false", price_change_percentage: "24h" }).toString();
      const upstream = await fetch(url, { headers: { accept: "application/json" }, signal: AbortSignal.timeout(7000) });
      if (!upstream.ok) return response.status(502).json({ error: "CoinGecko market directory is temporarily unavailable", source: "CoinGecko", upstreamStatus: upstream.status });
      const rows = normalizeCoinMarketRows(await upstream.json());
      return response.status(200).json({ source: "CoinGecko", dataType: "global_token_market_reference", page: paging.page, perPage: paging.perPage, rows, hasNextPage: rows.length === paging.perPage, chainCoverage: "Coin market rows are global references, not per-chain contract or exchange listing proofs." });
    } catch (error) {
      return response.status(502).json({ error: "CoinGecko market directory request failed", source: "CoinGecko", reason: error?.name || "RequestError" });
    }
  }
  if (source === "tokens") {
    const paging = parseUniversePage(request.query?.page, request.query?.perPage, 1000);
    if (!paging) return response.status(400).json({ error: "page must be 1–1000 and perPage must be 1–100" });
    try {
      if (!coinListCache || Date.now() >= coinListCacheUntil) {
        const upstream = await fetch("https://api.coingecko.com/api/v3/coins/list?include_platform=true", { headers: { accept: "application/json" }, signal: AbortSignal.timeout(10000) });
        if (!upstream.ok) return response.status(502).json({ error: "CoinGecko token/platform directory is temporarily unavailable", source: "CoinGecko", upstreamStatus: upstream.status });
        coinListCache = normalizeCoinListRows(await upstream.json());
        coinListCacheUntil = Date.now() + 10 * 60 * 1000;
      }
      const start = (paging.page - 1) * paging.perPage;
      const rows = coinListCache.slice(start, start + paging.perPage);
      return response.status(200).json({ source: "CoinGecko", dataType: "token_identity_and_platform_contract_directory", page: paging.page, perPage: paging.perPage, totalRows: coinListCache.length, rows, hasNextPage: start + rows.length < coinListCache.length, note: "Platform addresses are CoinGecko metadata. Verify chain, contract and exchange support before matching tickers." });
    } catch (error) {
      return response.status(502).json({ error: "CoinGecko token/platform directory request failed", source: "CoinGecko", reason: error?.name || "RequestError" });
    }
  }
  if (source === "venues") {
    const venueIds = typeof request.query?.venues === "string" ? [...new Set(request.query.venues.split(",").map((id) => id.trim()).filter(Boolean))] : ["binance", "bybit"];
    if (venueIds.length < 1 || venueIds.length > MAX_VENUES_PER_REQUEST || venueIds.some((id) => !EXCHANGE_BY_ID[id] || typeof ccxt[id] !== "function")) {
      return response.status(400).json({ error: `Select 1–${MAX_VENUES_PER_REQUEST} registered venues with available CCXT REST adapters`, registeredVenueCount: EXCHANGES.length });
    }
    const settled = await Promise.allSettled(venueIds.map(async (id) => {
      const exchange = new ccxt[id]({ enableRateLimit: true, timeout: 8000, options: { defaultType: "spot" } });
      try {
        await exchange.loadMarkets();
        const rows = normalizeVenueMarketRows(id, Object.values(exchange.markets));
        return { id, name: EXCHANGE_BY_ID[id].name, status: "available", totalSpotPairs: rows.length, rows };
      } finally { try { await exchange.close(); } catch {} }
    }));
    const venues = settled.map((result, index) => result.status === "fulfilled" ? result.value : ({ id: venueIds[index], name: EXCHANGE_BY_ID[venueIds[index]].name, status: "unavailable", totalSpotPairs: 0, rows: [], error: result.reason?.name || "RequestError" }));
    return response.status(200).json({ source: "ccxt_rest_market_catalog", dataType: "venue_listings_not_streaming_prices", venues, maxVenuesPerRequest: MAX_VENUES_PER_REQUEST, note: "Market catalogs verify listings, not persistent WebSocket health or token contract/network identity." });
  }
  return response.status(400).json({ error: "source must be coingecko or venues" });
}
