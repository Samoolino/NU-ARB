# Phase 14 — Deterministic paper/sandbox order transport

`arbx.paper_transport.PaperTransport` is an isolated, synchronous simulation
boundary over caller-supplied `MarketInfo`, `OrderBook`, and `OrderRequest`
models from `arbx.venue_contract`. It is not a venue adapter and is not wired
into the common opportunity/risk path, scanner, production execution
coordinator, or any live order route. It has no credential parameters, network
client, socket, authentication method, or `create_order`/`connect` API.
`paper` and `sandbox` are labels for independent local simulator instances;
`live` is explicitly rejected. No simulator credentials or transport can be
shared with a live adapter because neither is accepted by this module.

The simulator uses only explicit deterministic inputs:

- `FeeModel` specifies maker/taker quote fee rates.
- `DepthImpactModel` walks displayed book depth and applies configured
  side-aware impact as consumed-depth bps; filled liquidity is removed from
  the local book.
- `LatencyModel` advances an integer virtual clock. Ordered `BookMovement`
  inputs replace the book when their scheduled times are reached.
- `RateLimitModel`, `RejectionPolicy`, and `CancelFailurePolicy` describe
  explicit request-window limits and deterministic failure cases.
- Spot balance free/locked amounts are reserved at acceptance, filled against
  quote-denominated fees and price, released on completion/cancel, and exposed
  as typed `Balance` snapshots. Fills and resulting `Order` states are typed.
- `NetworkCostModel` is an explicit per-accepted-order quote cost. It is a
  local accounting assumption only, not a withdrawal, transfer, chain, or
  network operation.

There is no probabilistic mode in this phase; outcomes are reproducible from
the same starting book, balances, configuration, order sequence, and
simulated clock. A future probabilistic simulation would need an explicit seed
and is not implemented here.

Unsupported or unknown cases fail closed. Only explicitly active spot markets,
market/limit orders, and the documented TIF options are accepted. Derivatives,
reduce-only orders, unsupported options, wrong symbols, invalid books, and
attempts to select live mode are rejected rather than translated into
production capabilities.
This model does not certify exchange behavior, venue fees, order priority,
matching-engine queue position, external connectivity, or real network costs.
Partial market fills terminate the unfilled remainder; GTC limit remainders can
be processed again against subsequently available simulated depth. FOK orders
that cannot be fully filled reject without mutating the book or balances.

Offline checks:

```powershell
$env:PYTHONPATH = "arb_bot"
.\.venv\Scripts\python.exe -m pytest -q tests\test_paper_transport.py
.\.venv\Scripts\python.exe -m py_compile arb_bot\arbx\paper_transport.py tests\test_paper_transport.py
```
