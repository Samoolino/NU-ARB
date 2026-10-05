# Capital Injection Boundary & Capital Accounting Contract

## Purpose

This document defines the boundary between **capital** and **trading performance** for NU-ARB.

The engine may observe authenticated exchange balances and use already-funded balances for trading. It must not treat newly injected capital as realized trading profit, target progress, or execution revenue.

## 1. Capital injection definition

A **capital injection** is any increase in deployable account capital that is not causally attributable to an exchange-confirmed trade result.

Examples:
- external deposit from a wallet or bank/card funding path;
- manual transfer into an exchange account;
- capital added by the operator before or during a session;
- transfer from an external account/sub-account not represented by a completed NU-ARB execution;
- unexplained positive balance delta that cannot be reconciled to the execution ledger.

A capital injection is **not** a trade. It must never increase realized trading PnL, target-profit progress, modeled trade profitability, execution reward, or session performance attribution.

## 2. Internal inventory movement

A transfer between two exchange accounts owned by the trading operation is **inventory movement**, not trading PnL.

Transfers are outside the atomic trade loop; the engine may model/recommend rebalancing, but must not initiate a transfer as part of arbitrage execution. Transfer fees belong to the capital/inventory accounting layer unless explicitly attributed to a strategy by the accounting policy.

## 3. Capital baseline

At live-session engagement, establish an authenticated capital baseline per venue and asset: exchange, asset, free, used, total, observed timestamp, account-state version when available, and session ID.

The baseline is starting capital, not a deposit event and not PnL.

## 4. Balance-change classification

Every material balance change should be classified as one of:
1. `TRADE_RESULT`
2. `TRADING_FEE`
3. `EXTERNAL_INJECTION`
4. `INTERNAL_TRANSFER`
5. `EXTERNAL_WITHDRAWAL`
6. `FUNDING_OR_YIELD`
7. `ADJUSTMENT_OR_DUST`
8. `UNKNOWN`

Only `TRADE_RESULT` contributes to realized trading PnL. `UNKNOWN` must never be silently classified as profit.

## 5. Unknown-positive-delta rule

If an authenticated balance increases materially and cannot be reconciled to exchange-confirmed fills, known trading fees, known internal inventory movement, or an explicitly recorded capital event, the live engine must stop admitting new opportunities, persist the unexplained delta, mark capital reconciliation `EXCEPTION`, and require operator reconciliation before live execution resumes.

The engine must never use an unexplained balance increase to satisfy a profit target.

## 6. Injection during a live session

Capital added after `LIVE_ENGAGED` must not automatically become trading capacity.

Default policy: `CAPITAL_INJECTION_DETECTED -> NEW_ORDERS_HALTED -> OPERATOR_RECONCILIATION`. After explicit reconciliation, the operator may start a new session using the updated capital baseline.

## 7. Target accounting

Maintain separate quantities: `capital_baseline`, `capital_injected`, `capital_withdrawn`, `realized_trade_pnl`, `fees`, `net_strategy_pnl`, `current_verified_equity`, and `target_profit_remaining`.

The core profit target is `target_profit_remaining = target_profit - verified_realized_trade_pnl`. It must not be derived from arbitrary current equity minus starting balance.

A capital injection therefore cannot make the profit target appear closer to completion.

## 8. Target-equity semantics

`target_equity_usd` is an equity safety/terminal threshold, not a profit metric. If equity rises because of an injection, the system must not report that as earned performance.

When target equity is reached, default action is halt new trading, keep funds on the exchange, and require operator review. No automatic withdrawal is permitted.

## 9. Capital movement permissions

The trading engine may read balances, observe capital sources, and model transfer/rebalance. It may not initiate deposits, withdrawals, transfers, or move capital inside the atomic trade loop.

Live API keys should remain trade-only, with withdrawals and transfers disabled whenever the venue supports those restrictions.

## 10. Cross-exchange requirement

Cross-exchange arbitrage requires pre-funded inventory. Before acceptance, the buy venue needs sufficient quote balance, the sell venue sufficient base inventory, balances must be fresh, and reservations must prevent concurrent double-spend.

A transfer required to create inventory is not part of arbitrage execution.

## 11. Required durable records

The capital accounting layer should eventually persist `capital_baselines`, `capital_events`, and `capital_reconciliations`, including event type, exchange, asset, amount, before/after observations, source, reference, session, timestamps, operator, and status.

A capital event must be auditable independently from the trade journal.

## 12. Safety invariant

> **No external capital movement can create realized trading PnL, satisfy the trading-profit target, or silently increase live-session risk capacity.**

The exchange remains the source of truth for balances and fills. NU-ARB's capital ledger is an accounting and control layer, not a replacement for the exchange ledger.