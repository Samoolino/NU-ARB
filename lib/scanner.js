export function isValidSymbol(value) {
  return typeof value === "string" && /^[A-Z0-9]{2,16}USDT$/.test(value);
}

export function readFiniteNumber(value, fallback) {
  if (value === undefined || value === null || value === "") return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeLevels(levels, side) {
  if (!Array.isArray(levels)) throw new Error("Order-book side is missing");
  return levels.map((level) => {
    if (!Array.isArray(level) || level.length < 2) throw new Error("Malformed order-book level");
    const price = Number(level[0]);
    const amount = Number(level[1]);
    if (!Number.isFinite(price) || price <= 0 || !Number.isFinite(amount) || amount <= 0) {
      throw new Error("Order book contains an invalid price or quantity");
    }
    return { price, amount };
  }).sort((a, b) => side === "asks" ? a.price - b.price : b.price - a.price);
}

function buyWithQuote(levels, quoteBudget) {
  let quoteLeft = quoteBudget;
  let base = 0;
  let quote = 0;
  for (const { price, amount } of levels) {
    const takeBase = Math.min(amount, quoteLeft / price);
    if (takeBase <= 0) break;
    const takeQuote = takeBase * price;
    base += takeBase;
    quote += takeQuote;
    quoteLeft -= takeQuote;
    if (quoteLeft <= Math.max(1e-10, quoteBudget * 1e-12)) break;
  }
  return { base, quote, complete: quoteLeft <= Math.max(1e-8, quoteBudget * 1e-8) };
}

function sellBase(levels, baseSize) {
  let baseLeft = baseSize;
  let quote = 0;
  for (const { price, amount } of levels) {
    const takeBase = Math.min(amount, baseLeft);
    if (takeBase <= 0) break;
    quote += takeBase * price;
    baseLeft -= takeBase;
    if (baseLeft <= Math.max(1e-12, baseSize * 1e-12)) break;
  }
  return { quote, complete: baseLeft <= Math.max(1e-10, baseSize * 1e-8) };
}

function direction({ buyId, sellId, books, tradeSizeUsdt, feesBps }) {
  const buy = books[buyId];
  const sell = books[sellId];
  const asks = normalizeLevels(buy.asks, "asks");
  const bids = normalizeLevels(sell.bids, "bids");
  const purchase = buyWithQuote(asks, tradeSizeUsdt);
  if (!purchase.complete || purchase.base <= 0) {
    return { buyVenue: buyId, sellVenue: sellId, status: "insufficient_depth", netPnlUsdt: null };
  }
  const sale = sellBase(bids, purchase.base);
  if (!sale.complete) return { buyVenue: buyId, sellVenue: sellId, status: "insufficient_depth", netPnlUsdt: null };

  const buyFeeUsdt = purchase.quote * feesBps[buyId] / 10000;
  const sellFeeUsdt = sale.quote * feesBps[sellId] / 10000;
  const netPnlUsdt = sale.quote - sellFeeUsdt - purchase.quote - buyFeeUsdt;
  return {
    buyVenue: buyId,
    sellVenue: sellId,
    status: netPnlUsdt > 0 ? "positive_after_estimated_fees" : "no_net_edge",
    quantityBase: purchase.base,
    buyVwap: purchase.quote / purchase.base,
    sellVwap: sale.quote / purchase.base,
    buyNotionalUsdt: purchase.quote,
    sellNotionalUsdt: sale.quote,
    estimatedFeesUsd: buyFeeUsdt + sellFeeUsdt,
    netPnlUsdt,
    netReturnBps: purchase.quote > 0 ? netPnlUsdt / purchase.quote * 10000 : 0,
  };
}

export function scanBooks({ exchangeA, exchangeB, books, tradeSizeUsdt, feesBps }) {
  if (!exchangeA || !exchangeB || exchangeA === exchangeB) throw new Error("Choose two different exchanges");
  return [
    direction({ buyId: exchangeA, sellId: exchangeB, books, tradeSizeUsdt, feesBps }),
    direction({ buyId: exchangeB, sellId: exchangeA, books, tradeSizeUsdt, feesBps }),
  ];
}
