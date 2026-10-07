export const VENUE_STATES = Object.freeze([
  "CATALOGUED",
  "PUBLIC_MARKET_VERIFIED",
  "PUBLIC_WS_VERIFIED",
  "AUTHENTICATED",
  "BALANCE_VERIFIED",
  "PRIVATE_STREAM_VERIFIED",
  "PERMISSIONS_VERIFIED",
  "EXECUTION_ROUTE_VERIFIED",
  "LIVE_ELIGIBLE",
]);

const initialVerificationStates = Object.freeze(Object.fromEntries(
  VENUE_STATES.slice(1).map((state) => [state, false]),
));

function venue(id, controlId, name, engineSelectionSupported) {
  return Object.freeze({
    id,
    controlId,
    name,
    catalogState: "CATALOGUED",
    lifecycleState: "CATALOGUED",
    verificationStates: initialVerificationStates,
    engineSelectionSupported,
    engineSelectionAvailable: false,
    engineSelectionStatus: "UNAVAILABLE",
  });
}

// Catalog identities are metadata only; runtime evidence controls eligibility.
export const VENUE_CATALOG = Object.freeze([
  venue("binance", "binance", "Binance", true),
  venue("bybit", "bybit", "Bybit", true),
  venue("okx", "okx", "OKX", true),
  venue("kucoin", "kucoin", "KuCoin", true),
  venue("gateio", "gateio", "Gate.io", true),
  venue("bitget", "bitget", "Bitget", true),
  venue("kraken", "kraken", "Kraken", true),
  venue("coinbase", "coinbaseexchange", "Coinbase Exchange", true),
  venue("mexc", "mexc", "MEXC", true),
  venue("htx", "htx", "HTX", true),
  venue("bitfinex", "bitfinex", "Bitfinex", true),
  venue("cryptocom", "cryptocom", "Crypto.com Exchange", true),
  venue("coinex", "coinex", "CoinEx", true),
  venue("bitstamp", "bitstamp", "Bitstamp", true),
  venue("gemini", "gemini", "Gemini", true),
  venue("bingx", "bingx", "BingX", true),
  venue("lbank", "lbank", "LBank", true),
  venue("whitebit", "whitebit", "WhiteBIT", true),
  venue("bitmart", "bitmart", "BitMart", false),
  venue("upbit", "upbit", "Upbit", false),
]);

// Keep the existing public REST scanner's selectable set separate from the catalog.
export const EXCHANGES = Object.freeze(VENUE_CATALOG
  .filter((exchange) => exchange.engineSelectionSupported)
  .map((exchange) => Object.freeze({
    id: exchange.controlId,
    catalogId: exchange.id,
    name: exchange.name,
  })));

export const EXCHANGE_BY_ID = Object.freeze(Object.fromEntries(
  EXCHANGES.map((exchange) => [exchange.id, exchange]),
));

export const VENUE_BY_ID = Object.freeze(Object.fromEntries(
  VENUE_CATALOG.map((exchange) => [exchange.id, exchange]),
));
