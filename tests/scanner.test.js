import test from "node:test";
import assert from "node:assert/strict";
import { isValidSymbol, readFiniteNumber, scanBooks } from "../lib/scanner.js";

test("accepts plain spot symbols and rejects URL-like input", () => {
  assert.equal(isValidSymbol("BTCUSDT"), true);
  assert.equal(isValidSymbol("BTC-USDT"), false);\n  assert.equal(isValidSymbol("BTCUSDC"), false);
  assert.equal(isValidSymbol("https://example.com"), false);
});

test("parses bounded numeric inputs without coercing invalid values", () => {
  assert.equal(readFiniteNumber(undefined, 10), 10);
  assert.equal(readFiniteNumber("25.5", 10), 25.5);
  assert.equal(readFiniteNumber("NaN", 10), null);
});

test("uses ordered depth and subtracts both venue fees", () => {
  const binance = {
    asks: [["100", "1"], ["101", "2"]],
    bids: [["99", "2"], ["98", "2"]],
  };
  const bybit = {
    asks: [["103", "2"]],
    bids: [["102", "2"], ["100", "2"]],
  };
  const result = scanBooks({
    binance,
    bybit,
    tradeSizeUsd: 100,
    feesBps: { binance: 10, bybit: 10 },
  });
  assert.equal(result.length, 2);
  assert.equal(result[0].status, "positive_after_estimated_fees");
  assert.ok(result[0].netPnlUsd < 2);
  assert.equal(result[1].status, "no_net_edge");
});

test("marks opportunity unavailable when the sell book lacks matched depth", () => {
  const result = scanBooks({
    binance: { asks: [["100", "1"]], bids: [["99", "1"]] },
    bybit: { asks: [["101", "1"]], bids: [["100", "0.1"]] },
    tradeSizeUsd: 100,
    feesBps: { binance: 0, bybit: 0 },
  });
  assert.equal(result[0].status, "insufficient_depth");
});
