# Canonical venue-adapter contract

`arb_bot/arbx/venue_contract.py` defines the typed, package-level
`VenueAdapter` protocol and normalized data records for venue identity,
markets/tickers/order books/depth, account and order operations, fees/funding,
deposit addresses/withdrawals/transfers, health/latency, authentication,
permissions, and execution certification.

The contract separates a capability declaration from runtime evidence:

- `CapabilityStatus` is `unsupported`, `unverified`, or `implemented`.
- `EvidenceStatus` is `not_checked`, `verified`, `failed`, or `stale`.
- `CapabilityReport` stores declarations and evidence independently. Missing
  declarations default to **unverified** and missing evidence to
  **not checked**. No truthy defaults infer venue support.
- Authentication, permission, connectivity, and execution-certification
  results use explicit evidence records; verification alone is not trading
  authorization or live eligibility.

The contract remains separate from adapter factories, scanner, and execution
paths and defines no venue implementation or support claim. The additive
`arbx/public_market_certification.py` workflow consumes already-constructed
`VenueAdapter` instances for deterministic, read-only public certification.
It does not connect adapters or access credentials, balances, private streams,
permissions, or order methods. Live hybrid interfaces and execution behavior
remain in `arbx/hybrid/contracts.py` and `arbx/hybrid/adapters.py`.

## Phase 4: public-market certification

`certify_public_markets(adapters, symbol=...)` consumes a mapping of catalog
venue IDs to adapters. The caller must supply the adapter; the workflow does
not construct one, load credentials, or call `connect`. Required REST
capability declarations (`REST`, `MARKET_DISCOVERY`, `ORDER_BOOK`) must be
`implemented`. Certification calls only `get_markets()` and the REST
`get_order_book()` operation. Active (or activity-unspecified) spot market
metadata must match the requested symbol; the book must have valid bid and ask
levels, a venue timestamp and local receipt time within the configured age,
and latency within the configured limit. Sequence presence is reported
independently and is not inferred from timestamps.

Public WebSocket verification is optional. It calls `stream_order_book()` only
when both `PUBLIC_STREAM` and `ORDER_BOOK_STREAM` are declared implemented;
unsupported is reported `unavailable`, while an undeclared/unverified
capability remains `unverified`. A verified public REST pass sets only
`publicMarket` evidence; a valid first WS book may additionally set
`publicWebSocket`. Per-venue failures are isolated and adapter exception text
is omitted from result details.

These evidence bits advance only the catalog's `PUBLIC_MARKET_VERIFIED` and,
when applicable, `PUBLIC_WS_VERIFIED` milestones. The workflow always emits
false for authentication, balance, private-stream, and live-eligibility
evidence. No public-market pass can authorize scanning, execution, or live
trading. Venue/catalog entries stay unverified until a separate caller
explicitly supplies this fresh evidence to the catalog metadata projection.
This module has deterministic fake-adapter tests only; it does not make venue
network calls as part of tests or verification.

## Phase 5: read-only authenticated certification

`arbx/authenticated_certification.py` adds
`certify_authenticated_venues(credentials_by_venue, adapter_factory=...)`.
The caller supplies the already configured credential profiles and an
injected factory that constructs a `VenueAdapter` for each venue. There is no
default venue factory, and the workflow is intentionally not wired into the
production verification, scanner, or execution paths. Credential values are
passed only to that factory and are never included in results. Per-venue
failures are isolated and represented by fixed reason codes; raw exception
messages and evidence detail strings are not returned.

After connect/authentication verification, the workflow may read normalized
balances, verify the explicit `TRADE` permission assessment, check
`verify_private_stream()`, and inspect static `ORDER_CREATE` and `ORDER_CANCEL`
capability declarations. The implementation does not call any order method
(including create, cancel, fetch, or open-order methods). The adapter is
closed after the checks. The state report uses `verified`, `failed`, and
`unverified`. `AUTH_CONFIGURED` means a non-empty mapping of non-empty string
fields was supplied; the caller is responsible for passing a profile that was
validated against the Phase 3 venue/auth-mode schema.

`EXECUTION_API_OK` means only that both order and cancel methods are declared
implemented; it is not a route test. `EXECUTION_ROUTE_OK` and `LIVE_ELIGIBLE`
are deliberately always `unverified` with
`external_certification_required`. Authentication, balance, permission,
private-stream, and static capability evidence alone cannot promote either
state. This phase does not place orders, cancel orders, grant trading
authorization, or change existing production behavior.

Tests inject deterministic fake adapters only. No exchange credentials,
venue endpoints, or live orders are used by the Phase 5 test suite.

## Phase 6: deterministic order-book core

`arbx/venue_contract.py` adds typed `OrderBookSnapshot`, `OrderBookDelta`,
`TradePrint`, and feed/apply state models. `arbx/orderbook_core.py` provides a
stateful, single-market `OrderBookCore` that consumes those values and
caller-supplied monotonic nanosecond and wall-clock millisecond readings. It
does not perform REST/WebSocket calls, start background work, persist to disk,
or integrate with any adapter, scanner, or execution path. “Persistent” here
means the service retains and incrementally updates its in-process book; it is
not durable storage.

The caller explicitly transitions the feed through connect, connected,
disconnect/feed-loss, and reconnect operations. Reconnect backoff is
deterministic and exponential up to the configured cap; heartbeat expiry
requires a resnapshot. REST snapshots bootstrap the book. Deltas are accepted
only when their sequence range bridges the current sequence (or their explicit
previous sequence matches it and the range bridges the next sequence); gaps,
duplicate/out-of-order updates, malformed levels, stale source timestamps,
receive-time regressions, and excessive source/local clock skew invalidate the
current image and require a new snapshot. A retained image is never exposed as current while disconnected,
awaiting a snapshot, or outside monotonic/source-time/heartbeat freshness
limits.

Fresh books expose side-aware visible quote depth, quantity-based VWAP and
slippage estimates, spread, visible-depth imbalance, top-level microprice, and
rolling trade velocity. Metrics and estimates return no value for a
non-current book. These are descriptive market-data outputs only; they grant
no permission to scan, execute, or trade. Phase 6 tests use local deterministic
values and clocks only. No venue connectivity, adapter capability, runtime
verification, or production integration is claimed.
