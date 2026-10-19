import test from "node:test";
import assert from "node:assert/strict";
import { CoinGeckoReferenceProvider } from "../lib/coingecko-reference.js";

test("maps supported base assets to CoinGecko USD reference prices", async () => {
  let now = 1_800_000_000_000;
  const provider = new CoinGeckoReferenceProvider({now: () => now, fetchImpl: async () => ({ok: true, json: async () => ({bitcoin: {usd: 100000, last_updated_at: now / 1000}, ethereum: {usd: 3000, last_updated_at: now / 1000}})})});
  const result = await provider.getPrices(["BTC/USDT", "ETH/USDC", "FAKE/USDT", "BTC/USD"]);
  assert.equal(result.status, "available");
  assert.deepEqual(result.prices, {"BTC/USDT": 100000, "ETH/USDC": 3000});
  assert.equal(result.provider, "CoinGecko");
});

test("fails soft on outage and never reuses an expired reference price", async () => {
  let now = 1_800_000_000_000;
  let shouldFail = false;
  const provider = new CoinGeckoReferenceProvider({now: () => now, cacheTtlMs: 1000, fetchImpl: async () => {
    if (shouldFail) throw new Error("offline");
    return {ok: true, json: async () => ({bitcoin: {usd: 100000, last_updated_at: now / 1000}})};
  }});
  assert.equal((await provider.getPrices(["BTC/USDT"])).status, "available");
  now += 181_000;
  shouldFail = true;
  const result = await provider.getPrices(["BTC/USDT"]);
  assert.equal(result.status, "unavailable");
  assert.deepEqual(result.prices, {});
});
