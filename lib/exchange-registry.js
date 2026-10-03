export const EXCHANGES = Object.freeze([
  { id: "binance", name: "Binance" },
  { id: "bybit", name: "Bybit" },
  { id: "okx", name: "OKX" },
  { id: "kucoin", name: "KuCoin" },
  { id: "gateio", name: "Gate.io" },
  { id: "mexc", name: "MEXC" },
  { id: "htx", name: "HTX" },
  { id: "lbank", name: "LBank" },
  { id: "bitget", name: "Bitget" },
  { id: "kraken", name: "Kraken" },
  { id: "coinbaseexchange", name: "Coinbase Exchange" },
  { id: "bitfinex", name: "Bitfinex" },
  { id: "bitstamp", name: "Bitstamp" },
  { id: "gemini", name: "Gemini" },
  { id: "cryptocom", name: "Crypto.com Exchange" },
  { id: "coinex", name: "CoinEx" },
  { id: "bingx", name: "BingX" },
  { id: "whitebit", name: "WhiteBIT" },
]);

export const EXCHANGE_BY_ID = Object.freeze(Object.fromEntries(EXCHANGES.map((exchange) => [exchange.id, exchange])));
