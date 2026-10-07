# Phase 13 — Venue-reported settlement metadata

`arbx.hybrid.settlement_metadata` extends Phase 12's offline network catalog
with a pure parser for structured currency/network data already supplied by a
caller. It performs no exchange requests, credential handling, withdrawals, or
transfers. It is not connected to the scanner or order/execution paths.

Only these reported assets are accepted: USDT, USDC, BTC, ETH, BNB, SOL, AVAX,
MATIC/POL, ARB, OP, and TRX. `MATIC` and `POL` normalize to the canonical asset
`POL`; recognized network aliases normalize to the Phase 12 canonical network
identity (including `POL` as a Polygon alias). Unsupported assets and unknown
networks are ignored; no asset/network combinations are generated from the
catalog.

Input uses the structured shape `asset -> networks -> network metadata`. When
present and valid, the parser retains deposit/withdraw enabled flags, fee,
withdrawal minimum (including CCXT-style `limits.withdraw.min`), confirmations,
and contract address. A field absent from the input remains `None`; malformed
or conflicting aliases also remain unknown. Conflicting network identities
reject that reported network row. Duplicate rows for the same canonical
venue/asset/network merge only non-conflicting supplied values.

`SettlementMetadataRepository` writes to its own
`venue_settlement_metadata` SQLite table on a connection explicitly supplied by
its caller. It never opens a path or the production execution journal. Create a
separate connection for persistent use; tests use `sqlite3.connect(":memory:")`.
Reads are bounded to an exact venue/asset/network or one venue, and upserts
replace the latest observed fields (including replacing prior values with
unknown when the latest report omits them).

Venue-reported withdrawal availability is metadata only, not permission to
withdraw. Transfer status remains `UNVERIFIED`; withdrawal and transfer
operation flags are always disabled, and this phase adds no submission
interfaces. No transfer reachability, route, fee sufficiency, market, or
execution capability is implied.

Offline checks:

```powershell
python -m pytest -q tests/test_network_scope.py tests/test_settlement_metadata.py
python -m py_compile arb_bot/arbx/hybrid/networks.py arb_bot/arbx/hybrid/settlement_metadata.py tests/test_network_scope.py tests/test_settlement_metadata.py
```
