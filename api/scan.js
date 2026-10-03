import ccxt from "ccxt";
import { EXCHANGE_BY_ID } from "../lib/exchange-registry.js";
import { isValidSymbol, scanBooks, readFiniteNumber } from "../lib/scanner.js";

async function loadVenue(id, symbol) {
  const config = EXCHANGE_BY_ID[id];
  const Exchange = ccxt[id];
  if (!config || typeof Exchange !== "function") {
    throw new Error("This CCXT exchange adapter is unavailable in the deployed package.");
  }
  const exchange = new Exchange({
    enableRateLimit: true,
    timeout: 6000,
    options: { defaultType: "spot" },
  });
  const started = performance.now();
  try {
    await exchange.loadMarkets();
    const market = exchange.markets[symbol];
    if (!market || !(market.spot || market.type === "spot") || market.contract) {
      throw new Error("This exchange does not list this symbol as a spot market.");
    }
    if (!exchange.has.fetchOrderBook) throw new Error("CCXT reports no order-book method for this exchange.");
    const book = await exchange.fetchOrderBook(symbol, 100);
    if (!Array.isArray(book.asks) || !book.asks.length || !Array.isArray(book.bids) || !book.bids.length) {
      throw new Error("The exchange returned an empty order book.");
    }
    return {
      id,
      name: config.name,
      status: "live",
      latencyMs: Math.round(performance.now() - started),
      timestamp: book.timestamp ? new Date(book.timestamp).toISOString() : null,
      receivedAt: Date.now(),
      asks: book.asks,
      bids: book.bids,
    };
  } finally {
    try { await exchange.close(); } catch {}
  }
}

export default async function handler(request, response) {
  if (request.method !== "GET") {
    response.setHeader("Allow", "GET");
    return response.status(405).json({ error: "Use GET" });
  }

  const symbolInput = String(request.query?.symbol || "BTCUSDT").trim().toUpperCase();
  const exchangeA = String(request.query?.exchangeA || "binance").trim().toLowerCase();
  const exchangeB = String(request.query?.exchangeB || "bybit").trim().toLowerCase();
  const tradeSizeUsdt = readFiniteNumber(request.query?.size, 100);
  const feeA = readFiniteNumber(request.query?.feeA, 10);
  const feeB = readFiniteNumber(request.query?.feeB, 10);

  if (!isValidSymbol(symbolInput)) {
    return response.status(400).json({ error: "Use a spot symbol quoted in USDT, such as BTCUSDT." });
  }
  if (!EXCHANGE_BY_ID[exchangeA] || !EXCHANGE_BY_ID[exchangeB] || exchangeA === exchangeB) {
    return response.status(400).json({ error: "Choose two different supported exchanges." });
  }
  if (tradeSizeUsdt === null || tradeSizeUsdt < 10 || tradeSizeUsdt > 100000) {
    return response.status(400).json({ error: "Trade size must be between 10 and 100,000 USDT." });
  }
  if ([feeA, feeB].some((fee) => fee === null || fee < 0 || fee > 100)) {
    return response.status(400).json({ error: "Each taker fee must be between 0 and 100 basis points." });
  }

  response.setHeader("Cache-Control", "no-store, max-age=0");
  response.setHeader("X-Content-Type-Options", "nosniff");

  const symbol = symbolInput.slice(0, -4) + "/USDT";
  const selected = [exchangeA, exchangeB];
  const results = await Promise.allSettled(selected.map((id) => loadVenue(id, symbol)));
  const venues = results.map((result, index) => result.status === "fulfilled"
    ? result.value
    : {
        id: selected[index],
        name: EXCHANGE_BY_ID[selected[index]].name,
        status: "unavailable",
        error: result.reason?.name === "RequestTimeout" || result.reason?.name === "TimeoutError"
          ? "Public API request timed out"
          : (result.reason?.message || "Public API request failed"),
      });
  const live = Object.fromEntries(venues.filter((venue) => venue.status === "live").map((venue) => [venue.id, venue]));
  const opportunities = live[exchangeA] && live[exchangeB]
    ? scanBooks({
        exchangeA,
        exchangeB,
        books: Object.fromEntries(Object.entries(live).map(([id, venue]) => [id, venue])),
        tradeSizeUsdt,
        feesBps: { [exchangeA]: feeA, [exchangeB]: feeB },
      })
    : [];

  return response.status(200).json({
    symbol,
    quote: "USDT",
    observedAt: new Date().toISOString(),
    dataMode: "public_rest_snapshot",
    executionEnabled: false,
    venues: venues.map(({ asks, bids, receivedAt, ...venue }) => ({
      ...venue,
      receivedAt: receivedAt ? new Date(receivedAt).toISOString() : null,
    })),
    books: Object.fromEntries(Object.entries(live).map(([id, venue]) => [id, {
      bestBid: Number(venue.bids[0][0]),
      bestAsk: Number(venue.asks[0][0]),
      receivedAt: new Date(venue.receivedAt).toISOString(),
    }])),
    opportunities,
  });
}
