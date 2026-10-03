import { isValidSymbol, scanBooks, readFiniteNumber } from "../lib/scanner.js";

const VENUES = [
  {
    id: "binance",
    name: "Binance",
    url: (symbol) => `https://api.binance.com/api/v3/depth?symbol=${symbol}&limit=1000`,
    parse: (body, receivedAt) => {
      if (!Array.isArray(body?.asks) || !Array.isArray(body?.bids)) throw new Error("Unexpected Binance order-book response");
      return { asks: body.asks, bids: body.bids, exchangeTime: null, receivedAt };
    },
  },
  {
    id: "bybit",
    name: "Bybit",
    url: (symbol) => `https://api.bybit.com/v5/market/orderbook?category=spot&symbol=${symbol}&limit=200`,
    parse: (body, receivedAt) => {
      if (body?.retCode !== 0 || !Array.isArray(body?.result?.a) || !Array.isArray(body?.result?.b)) {
        throw new Error(body?.retMsg || "Unexpected Bybit order-book response");
      }
      return { asks: body.result.a, bids: body.result.b, exchangeTime: Number(body.time) || null, receivedAt };
    },
  },
];

async function loadVenue(venue, symbol) {
  const started = performance.now();
  const response = await fetch(venue.url(symbol), {
    headers: { accept: "application/json" },
    signal: AbortSignal.timeout(7000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Public API returned HTTP ${response.status}`);
  const body = await response.json();
  const receivedAt = Date.now();
  const book = venue.parse(body, receivedAt);
  if (!book.asks.length || !book.bids.length) throw new Error("The exchange returned an empty order book");
  return {
    id: venue.id,
    name: venue.name,
    status: "live",
    latencyMs: Math.round(performance.now() - started),
    receivedAt,
    exchangeTime: book.exchangeTime,
    asks: book.asks,
    bids: book.bids,
  };
}

export default async function handler(request, response) {
  if (request.method !== "GET") {
    response.setHeader("Allow", "GET");
    return response.status(405).json({ error: "Use GET" });
  }

  const symbol = String(request.query?.symbol || "BTCUSDT").trim().toUpperCase();
  const tradeSizeUsd = readFiniteNumber(request.query?.size, 100);
  const binanceFeeBps = readFiniteNumber(request.query?.binanceFeeBps, 10);
  const bybitFeeBps = readFiniteNumber(request.query?.bybitFeeBps, 10);

  if (!isValidSymbol(symbol)) return response.status(400).json({ error: "Use a spot symbol quoted in USDT, such as BTCUSDT." });
  if (tradeSizeUsd === null || tradeSizeUsd < 10 || tradeSizeUsd > 100000) {
    return response.status(400).json({ error: "Trade size must be between 10 and 100,000 USDT." });
  }
  if ([binanceFeeBps, bybitFeeBps].some((fee) => fee === null || fee < 0 || fee > 100)) {
    return response.status(400).json({ error: "Each taker fee must be between 0 and 100 basis points." });
  }

  response.setHeader("Cache-Control", "no-store, max-age=0");
  response.setHeader("X-Content-Type-Options", "nosniff");

  const results = await Promise.allSettled(VENUES.map((venue) => loadVenue(venue, symbol)));
  const venues = results.map((result, index) => result.status === "fulfilled"
    ? result.value
    : { id: VENUES[index].id, name: VENUES[index].name, status: "unavailable", error: result.reason?.name === "TimeoutError" ? "Public API request timed out" : (result.reason?.message || "Public API request failed") });

  const live = Object.fromEntries(venues.filter((venue) => venue.status === "live").map((venue) => [venue.id, venue]));
  const opportunities = live.binance && live.bybit
    ? scanBooks({
        binance: live.binance,
        bybit: live.bybit,
        tradeSizeUsd,
        feesBps: { binance: binanceFeeBps, bybit: bybitFeeBps },
      })
    : [];

  return response.status(200).json({
    symbol,
    quote: "USDT",
    observedAt: new Date().toISOString(),
    dataMode: "public_rest_snapshot",
    executionEnabled: false,
    venues: venues.map(({ asks, bids, ...venue }) => venue),
    books: Object.fromEntries(Object.entries(live).map(([id, venue]) => [id, {
      bestBid: Number(venue.bids[0][0]),
      bestAsk: Number(venue.asks[0][0]),
      receivedAt: new Date(venue.receivedAt).toISOString(),
    }])),
    opportunities,
  });
}
