export const MAX_UNIVERSE_PAGE_SIZE = 100;
export const MAX_UNIVERSE_PAGE = 100;
export const SUPPORTED_QUOTES = new Set(["USDT", "USDC", "DAI"]);

export function parseUniversePage(pageValue, sizeValue, maxPage = MAX_UNIVERSE_PAGE) {
  const page = Number(pageValue ?? 1);
  const perPage = Number(sizeValue ?? 50);
  if (!Number.isInteger(page) || page < 1 || page > maxPage) return null;
  if (!Number.isInteger(perPage) || perPage < 1 || perPage > MAX_UNIVERSE_PAGE_SIZE) return null;
  return { page, perPage };
}

export function normalizeCoinMarketRows(rows) {
  if (!Array.isArray(rows)) return [];
  return rows.filter((coin) => coin && typeof coin.id === "string" && typeof coin.symbol === "string" && typeof coin.name === "string")
    .map((coin) => ({
      id: coin.id,
      symbol: coin.symbol.toUpperCase(),
      name: coin.name,
      image: typeof coin.image === "string" ? coin.image : null,
      currentPriceUsd: Number.isFinite(coin.current_price) ? coin.current_price : null,
      marketCapUsd: Number.isFinite(coin.market_cap) ? coin.market_cap : null,
      marketCapRank: Number.isInteger(coin.market_cap_rank) ? coin.market_cap_rank : null,
      volume24hUsd: Number.isFinite(coin.total_volume) ? coin.total_volume : null,
      change24hPct: Number.isFinite(coin.price_change_percentage_24h) ? coin.price_change_percentage_24h : null,
      // A ticker is only a candidate. Exchange listings and token contract/platform identity must be verified separately.
      candidateSpotPairs: SUPPORTED_QUOTES.has("USDT") && /^[A-Z0-9]{2,20}$/.test(String(coin.symbol).toUpperCase())
        ? [`${String(coin.symbol).toUpperCase()}/USDT`, `${String(coin.symbol).toUpperCase()}/USDC`, `${String(coin.symbol).toUpperCase()}/DAI`]
        : [],
    }));
}

export function normalizeVenueMarketRows(venueId, markets) {
  if (!Array.isArray(markets)) return [];
  return markets.filter((market) => market && market.active !== false && market.spot !== false && market.contract !== true &&
    typeof market.symbol === "string" && typeof market.base === "string" && typeof market.quote === "string" && SUPPORTED_QUOTES.has(market.quote))
    .map((market) => ({ venue: venueId, symbol: market.symbol, base: market.base, quote: market.quote, active: market.active !== false }));
}

export function normalizeCoinListRows(rows) {
  if (!Array.isArray(rows)) return [];
  return rows.filter((coin) => coin && typeof coin.id === "string" && typeof coin.symbol === "string" && typeof coin.name === "string")
    .map((coin) => {
      const platforms = coin.platforms && typeof coin.platforms === "object" && !Array.isArray(coin.platforms) ? coin.platforms : {};
      const entries = Object.entries(platforms).filter(([network, address]) => typeof network === "string" && network && typeof address === "string")
        .map(([network, address]) => ({ network, contractAddress: address }));
      return { id: coin.id, symbol: coin.symbol.toUpperCase(), name: coin.name, platforms: entries, platformCount: entries.length };
    });
}
