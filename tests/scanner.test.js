import test from "node:test";
import assert from "node:assert/strict";
import { isValidSymbol, readFiniteNumber, scanBooks } from "../lib/scanner.js";
import { EXCHANGES, EXCHANGE_BY_ID } from "../lib/exchange-registry.js";

test("registers the 18 requested public spot venues without duplicate ids", () => {
  assert.equal(EXCHANGES.length, 18);
  assert.equal(new Set(EXCHANGES.map((exchange) => exchange.id)).size, 18);
  assert.equal(EXCHANGE_BY_ID.cryptocom.name, "Crypto.com Exchange");
});

test("accepts only plain USDT-quoted spot symbols", () => {
  assert.equal(isValidSymbol("BTCUSDT"), true);
  assert.equal(isValidSymbol("BTC-USDT"), false);
  assert.equal(isValidSymbol("BTCUSDC"), false);
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
