# ARBX 2.0 - multi-exchange triangular + cross-exchange arbitrage engine

**Read first.** This code enforces a modeled pre-trade minimum floor and post-trade verification. It does **not** and cannot assure realized profit: fee-tier differences, latency, partial fills, non-atomic cross-venue orders, and unwind prices can still create losses. On liquid spot markets, most scans show no edge.

## Live pilot condition: $3 starter capital

The live pilot now uses a fixed **$3 starter-capital allocation**. The previous `$25 maximum order` and separate `$3 session-loss halt` are no longer the live risk condition.

Every live configuration path is normalized to:

```text
starter capital = $3
engagement size = $3 maximum
session-loss ceiling = none
fixed profit target = none
```

`BOT_MAX_LOSS_USD`, `BOT_TRADE_SIZE_USD`, and `BOT_TARGET_PROFIT_USD` remain accepted by older control-plane/launcher paths for compatibility, but live `Config.validate()` normalizes them to the $3 starter-capital condition. The live risk gate no longer checks cumulative session loss.

The $3 condition does not guarantee profit or cap losses from partial fills, exchange outages, stale liquidity, adverse movement, or unwind execution. Live trading still requires verified exchange connectivity, permissions, balances, private/public streams, latency, liquidity, modeled net profitability and explicit live confirmation.

## Modeled-profit gate

For every trade, before any order is sent:
1. Books must be fresh and REST RTT healthy.
2. Depth-walked expected edge must clear `min_net_bps` after fees.
3. Each leg receives an IOC limit price.
4. The modeled worst-case floor must clear `min_worst_bps` and `min_profit_usd`.
5. Exchange minimums, balances/inventory, trade-rate limits and the execution circuit breaker must pass.

After every trade, realized PnL is compared with the modeled floor. A sufficiently large modeled-floor miss can still halt the engine for recovery.

## Core layers

| Layer | Job |
|---|---|
| Config | environment settings, live-mode validation and starter capital |
| Discovery | spot markets, triangular paths and cross-exchange opportunities |
| Streams | persistent public/private market and account streams |
| Strategy | depth-aware VWAP and worst-case cycle math |
| Gate | modeled profitability and execution safety |
| Execution | paper/live IOC execution, fills and unwind handling |
| Orchestration | exchange workers, settlement, verification and journal |
| Transfers | manual inventory rebalancing plans; never part of the trade loop |

## Live verification requirements

- At least two verified CEX venues for cross-exchange live mode.
- Read + Spot Trade permissions only.
- Withdrawals/transfers disabled.
- Fresh authenticated balances.
- Public and private WebSocket verification where supported.
- IOC execution capability verified.
- Latency inside the configured live threshold.
- Pre-funded inventory on both sides for cross-exchange execution.

## Paper mode

Use paper trading with real market data before live operation. Review the durable trade journal and compare modeled versus realized outcomes. Do not loosen profitability gates merely to create trades.

## Operational note

The current PowerShell launcher still accepts legacy notional/loss inputs for compatibility, but the live configuration layer ignores those values and normalizes the live engine to the fixed $3 starter-capital condition.
