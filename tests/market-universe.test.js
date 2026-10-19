import test from "node:test";
import assert from "node:assert/strict";
import { normalizeCoinListRows, normalizeCoinMarketRows, normalizeVenueMarketRows, parseUniversePage } from "../lib/market-universe.js";

test("bounds CoinGecko directory pagination", () => {
  assert.deepEqual(parseUniversePage("2", "100"), {page: 2, perPage: 100});
  assert.equal(parseUniversePage("0", "50"), null);
  assert.equal(parseUniversePage("101", "50"), null);
  assert.equal(parseUniversePage("1", "101"), null);
});

test("normalizes CoinGecko market references without claiming exchange listing verification", () => {
  const rows = normalizeCoinMarketRows([{id: "bitcoin", symbol: "btc", name: "Bitcoin", current_price: 80000, market_cap_rank: 1, total_volume: 10, price_change_percentage_24h: 2}, null]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].symbol, "BTC");
  assert.deepEqual(rows[0].candidateSpotPairs, ["BTC/USDT", "BTC/USDC", "BTC/DAI"]);
  assert.equal(rows[0].currentPriceUsd, 80000);
});

test("venue catalog contains only active spot pairs with supported stable quotes", () => {
  const rows = normalizeVenueMarketRows("binance", [
    {symbol: "BTC/USDT", base: "BTC", quote: "USDT", spot: true, active: true},
    {symbol: "BTC/USD", base: "BTC", quote: "USD", spot: true, active: true},
    {symbol: "BTC/USDT:USDT", base: "BTC", quote: "USDT", spot: false, contract: true, active: true},
    {symbol: "OLD/USDT", base: "OLD", quote: "USDT", spot: true, active: false},
  ]);
  assert.deepEqual(rows.map((row) => row.symbol), ["BTC/USDT"]);
});

test("normalizes CoinGecko token platform contracts without assuming ticker identity", () => {
  const rows = normalizeCoinListRows([{id:"wrapped-token",symbol:"wtx",name:"Wrapped Token",platforms:{ethereum:"0xabc",polygon_pos:"0xdef",empty:null}}]);
  assert.equal(rows.length,1);
  assert.equal(rows[0].symbol,"WTX");
  assert.equal(rows[0].platformCount,2);
  assert.deepEqual(rows[0].platforms,[{network:"ethereum",contractAddress:"0xabc"},{network:"polygon_pos",contractAddress:"0xdef"}]);
});

test("supports bounded paging for large token/platform catalogs", () => {
  assert.deepEqual(parseUniversePage("1000","100",1000),{page:1000,perPage:100});
  assert.equal(parseUniversePage("1001","100",1000),null);
});
