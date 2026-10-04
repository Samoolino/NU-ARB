import ccxt from "ccxt";
import { EXCHANGES, EXCHANGE_BY_ID } from "../lib/exchange-registry.js";
import { isValidSymbol, readFiniteNumber, scanAllVenues } from "../lib/scanner.js";

const MAX_MARKETS = 12;
const MAX_NOTIONAL_USD = 100000;

async function loadVenue(id, symbols) {
  const config = EXCHANGE_BY_ID[id];
  const Exchange = ccxt[id];
  if (!config || typeof Exchange !== "function") {
    throw new Error("This CCXT exchange adapter is unavailable in the deployed package.");
  }
  const exchange = new Exchange({
    enableRateLimit: true,
    timeout: 8000,
    options: {defaultType: "spot"},
  });
  const started = performance.now();
  try {
    await exchange.loadMarkets();
    const available = symbols.filter((symbol) => {
      const market = exchange.markets[symbol];
      return market && (market.spot || market.type === "spot") && !market.contract &&
        exchange.has.fetchOrderBook;
    });
    if (!available.length) {
      throw new Error("No selected spot markets are listed by this venue.");
    }
    const settled = await Promise.allSettled(available.map(async (symbol) => {
      const book = await exchange.fetchOrderBook(symbol, 20);
      if (!Array.isArray(book.asks) || !book.asks.length || !Array.isArray(book.bids) || !book.bids.length) {
        throw new Error("The spot order book is empty.");
      }
      return [symbol, {asks: book.asks, bids: book.bids, timestamp: book.timestamp}];
    }));
    const books = {};
    const errors = {};
    for (let index = 0; index < settled.length; index++) {
      const result = settled[index];
      if (result.status === "fulfilled") books[result.value[0]] = result.value[1];
      else errors[available[index]] = result.reason?.name || "RequestError";
    }
    return {
      id,
      name: config.name,
      status: Object.keys(books).length ? "available" : "unavailable",
      latencyMs: Math.round(performance.now() - started),
      markets: Object.keys(books),
      books,
      errors,
    };
  } finally {
    try { await exchange.close(); } catch {}
  }
}

function parseList(value, fallback) {
  if (typeof value !== "string") return fallback;
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

export default async function handler(request, response) {
  if (request.method !== "GET") {
    response.setHeader("Allow", "GET");
    return response.status(405).json({error: "Use GET"});
  }

  const venueIds = parseList(request.query?.venues, ["binance", "bybit"]);
  const symbols = parseList(request.query?.symbols, ["BTC/USDT"]);
  const notionalUsd = readFiniteNumber(request.query?.notionalUsd, 100);
  const takerFeeBps = readFiniteNumber(request.query?.takerFeeBps, 10);
  const minNetBps = readFiniteNumber(request.query?.minNetBps, 5);

  if (venueIds.length < 2 || venueIds.length > EXCHANGES.length ||
      new Set(venueIds).size !== venueIds.length ||
      venueIds.some((id) => !EXCHANGE_BY_ID[id])) {
    return response.status(400).json({error: "Select 2–18 distinct registered exchanges."});
  }
  if (symbols.length < 1 || symbols.length > MAX_MARKETS ||
      new Set(symbols).size !== symbols.length || symbols.some((symbol) => !isValidSymbol(symbol))) {
    return response.status(400).json({error: `Select 1–${MAX_MARKETS} distinct BASE/USDT, BASE/USDC, or BASE/DAI spot pairs.`});
  }
  if (notionalUsd === null || notionalUsd < 1 || notionalUsd > MAX_NOTIONAL_USD) {
    return response.status(400).json({error: `Notional must be between 1 and ${MAX_NOTIONAL_USD} USD-equivalent.`});
  }
  if (takerFeeBps === null || takerFeeBps < 0 || takerFeeBps > 100) {
    return response.status(400).json({error: "Assumed taker fee must be between 0 and 100 bps."});
  }
  if (minNetBps === null || minNetBps < 0 || minNetBps > 500) {
    return response.status(400).json({error: "Minimum estimated net edge must be between 0 and 500 bps."});
  }

  response.setHeader("Cache-Control", "no-store, max-age=0");
  response.setHeader("X-Content-Type-Options", "nosniff");
  const settled = await Promise.allSettled(venueIds.map((id) => loadVenue(id, symbols)));
  const venues = settled.map((result, index) => result.status === "fulfilled"
    ? result.value
    : {
        id: venueIds[index],
        name: EXCHANGE_BY_ID[venueIds[index]].name,
        status: "unavailable",
        latencyMs: 0,
        markets: [],
        books: {},
        errors: {venue: result.reason?.name || "RequestError"},
      });
  const booksBySymbol = Object.fromEntries(symbols.map((symbol) => [symbol, {}]));
  for (const venue of venues) {
    for (const [symbol, book] of Object.entries(venue.books)) booksBySymbol[symbol][venue.id] = book;
  }
  const opportunities = scanAllVenues({
    venueIds,
    symbols,
    booksBySymbol,
    notionalUsd,
    feesBps: Object.fromEntries(venueIds.map((id) => [id, takerFeeBps])),
    minNetBps,
  });

  return response.status(200).json({
    observedAt: Date.now() / 1000,
    dataMode: "public_rest_snapshot",
    executionEnabled: false,
    notionalUsd,
    takerFeeBps,
    minNetBps,
    venues: venues.map(({books, ...venue}) => venue),
    opportunities,
  });
}
