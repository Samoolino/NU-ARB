# $3 Profit-Compounding DCA Live Policy

## Purpose

Live CEX execution starts with exactly **$3 starter capital**. The engine may increase the next engagement only from **realized positive P&L**.

This is a profit-compounding policy, not a martingale or averaging-down system. A losing position is never enlarged by the strategy.

## Execution rule

Every live opportunity must pass the existing modeled-profit gate:

- fresh order books
- healthy latency
- sufficient depth and exchange minimums
- positive modeled net edge after fees
- positive modeled worst-case USD result
- sufficient inventory
- explicit live cross-exchange authorization

If the modeled result is not strictly positive, the opportunity is rejected.

## Capital progression

The next trade's target notional is:

`starter capital + cumulative realized positive P&L`

Example:

| Realized P&L | Next target capital |
|---:|---:|
| $0.00 | $3.00 |
| +$0.10 | $3.10 |
| +$0.25 | $3.25 |
| +$0.75 | $3.75 |

Actual exchange inventory remains the hard ceiling, so the engine cannot spend more than the funds actually available on the required venue(s).

## Loss behavior

There is no separate `$3 session-loss` setting anymore.

If a live trade is actually realized at a loss, the engine immediately enters a halt state. It does not attempt to recover the loss by increasing the next trade size.

A failed or unfilled IOC cycle is also subject to the existing consecutive-failure circuit breaker and does not count as profit.

## Important limitation

A positive modeled floor is a pre-trade economic condition, not a guarantee that a live cross-exchange trade cannot lose money. Cross-venue execution is non-atomic and partial fills, fee differences, latency, and unwind prices can create realized losses. The loss circuit breaker therefore exists to stop subsequent engagements after the first realized loss.

## Strategy naming

The live configuration uses `profit_dca` to describe this policy. It should **not** be interpreted as averaging down a losing asset position. The implementation is staged/progressive capital deployment using realized profits only.
