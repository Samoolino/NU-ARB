import test from "node:test";
import assert from "node:assert/strict";
import { isValidSymbol, readFiniteNumber, scanBooks } from "../lib/scanner.js";
import {
  EXCHANGES,
  EXCHANGE_BY_ID,
  VENUE_CATALOG,
  VENUE_STATES,
  VENUE_BY_ID,
} from "../lib/exchange-registry.js";

const CATALOG_IDS = [
  "binance", "bybit", "okx", "kucoin", "gateio", "bitget", "kraken", "coinbase",
  "mexc", "htx", "bitfinex", "cryptocom", "coinex", "bitstamp", "gemini", "bingx",
  "lbank", "whitebit", "bitmart", "upbit",
];

test("registers the 18 requested public spot venues without duplicate ids", () => {
  assert.equal(EXCHANGES.length, 18);
  assert.equal(new Set(EXCHANGES.map((exchange) => exchange.id)).size, 18);
  assert.equal(EXCHANGE_BY_ID.cryptocom.name, "Crypto.com Exchange");
});

test("catalogs all 20 canonical identities with unverified, unavailable defaults", () => {
  assert.deepEqual(VENUE_CATALOG.map((exchange) => exchange.id), CATALOG_IDS);
  assert.deepEqual(VENUE_STATES, [
    "CATALOGUED", "PUBLIC_MARKET_VERIFIED", "PUBLIC_WS_VERIFIED", "AUTHENTICATED",
    "BALANCE_VERIFIED", "PRIVATE_STREAM_VERIFIED", "PERMISSIONS_VERIFIED",
    "EXECUTION_ROUTE_VERIFIED", "LIVE_ELIGIBLE",
  ]);
  for (const exchange of VENUE_CATALOG) {
    assert.equal(exchange.catalogState, "CATALOGUED");
    assert.equal(exchange.lifecycleState, "CATALOGUED");
    assert.deepEqual(exchange.verificationStates, Object.fromEntries(
      VENUE_STATES.slice(1).map((state) => [state, false]),
    ));
    assert.equal(exchange.engineSelectionAvailable, false);
    assert.equal(exchange.engineSelectionStatus, "UNAVAILABLE");
  }
  assert.equal(VENUE_BY_ID.coinbase.controlId, "coinbaseexchange");
});

test("catalog-only venues do not enter public scanner or control-engine selection sets", () => {
  for (const id of ["bitmart", "upbit"]) {
    assert.equal(VENUE_BY_ID[id].engineSelectionSupported, false);
    assert.equal(VENUE_CATALOG.some((exchange) => exchange.controlId === id && exchange.engineSelectionSupported), false);
    assert.equal(EXCHANGES.some((exchange) => exchange.id === id), false);
  }
  assert.equal(EXCHANGES.length, 18);
});

test("accepts spot market pairs quoted in supported stablecoins", () => {
  assert.equal(isValidSymbol("BTC/USDT"), true);
  assert.equal(isValidSymbol("BTC/USDC"), true);
  assert.equal(isValidSymbol("ETH/DAI"), true);
  assert.equal(isValidSymbol("BTCUSDT"), false);
  assert.equal(isValidSymbol("BTC-USDT"), false);
  assert.equal(isValidSymbol("BTC/USD"), false);
  assert.equal(isValidSymbol("https://example.com"), false);
});

test("parses finite numeric inputs without coercing invalid values", () => {
  assert.equal(readFiniteNumber(undefined, 10), 10);
  assert.equal(readFiniteNumber("25.5", 10), 25.5);
  assert.equal(readFiniteNumber("NaN", 10), null);
});

test("uses ordered depth and subtracts fees at both selected venues", () => {
  const books = {
    binance: { asks: [["100", "1"], ["101", "2"]], bids: [["99", "2"], ["98", "2"]] },
    bybit: { asks: [["103", "2"]], bids: [["102", "2"], ["100", "2"]] },
  };
  const result = scanBooks({
    exchangeA: "binance",
    exchangeB: "bybit",
    books,
    tradeSizeUsdt: 100,
    feesBps: { binance: 10, bybit: 10 },
  });
  assert.equal(result.length, 2);
  assert.equal(result[0].status, "positive_after_estimated_fees");
  assert.ok(result[0].netPnlUsdt < 2);
  assert.equal(result[1].status, "no_net_edge");
});

test("marks a direction unavailable when the sell book lacks matched depth", () => {
  const result = scanBooks({
    exchangeA: "binance",
    exchangeB: "bybit",
    books: {
      binance: { asks: [["100", "1"]], bids: [["99", "1"]] },
      bybit: { asks: [["101", "1"]], bids: [["100", "0.1"]] },
    },
    tradeSizeUsdt: 100,
    feesBps: { binance: 0, bybit: 0 },
  });
  assert.equal(result[0].status, "insufficient_depth");
});

test("requires distinct selected exchanges", () => {
  assert.throws(() => scanBooks({ exchangeA: "binance", exchangeB: "binance", books: {}, tradeSizeUsdt: 100, feesBps: {} }));
});

test("ranks multi-pair opportunities across every selected venue and applies the BPS floor", async () => {
  const { scanAllVenues } = await import("../lib/scanner.js");
  const opportunities = scanAllVenues({
    venueIds: ["binance", "bybit", "okx"],
    symbols: ["BTC/USDT", "ETH/USDC"],
    booksBySymbol: {
      "BTC/USDT": {
        binance: { asks: [[100, 2]], bids: [[99, 2]] },
        bybit: { asks: [[103, 2]], bids: [[102, 2]] },
        okx: { asks: [[102, 2]], bids: [[101, 2]] },
      },
      "ETH/USDC": {
        binance: { asks: [[10, 20]], bids: [[9.9, 20]] },
        bybit: { asks: [[11, 20]], bids: [[10.9, 20]] },
        okx: { asks: [[10.8, 20]], bids: [[10.7, 20]] },
      },
    },
    notionalUsd: 100,
    feesBps: { binance: 10, bybit: 10, okx: 10 },
    minNetBps: 5,
  });

  assert.ok(opportunities.length > 2);
  assert.equal(opportunities[0].symbol, "ETH/USDC");
  assert.equal(opportunities[0].buyVenue, "binance");
  assert.equal(opportunities[0].sellVenue, "bybit");
  assert.equal(opportunities[0].status, "meets_minimum_estimated_edge");
  assert.ok(opportunities.some((opportunity) => opportunity.symbol === "BTC/USDT"));
  assert.ok(opportunities.every((opportunity) => opportunity.executionEnabled === false));
});
