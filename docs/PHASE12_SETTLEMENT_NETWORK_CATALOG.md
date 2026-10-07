# Phase 12 — Settlement-network catalog discovery

`arbx.hybrid.networks` keeps its exact 20 canonical network identities and
normalizes their known aliases. Its parser accepts an already-provided
structured currency report (currency mapping → `networks` mapping → network
metadata) and emits immutable venue/asset/network settlement dimensions for
recognized networks. It is a pure parser: it makes no exchange, transfer,
withdrawal, or other network calls.

`VENUE_REPORTED` means only that the network appeared in structured metadata
provided for that venue. Being present in the catalog is not venue evidence.
Absent, malformed, unknown, or conflicting report entries produce no
observation. Reports are parsed per venue and do not share or promote state.
Market, deposit, withdrawal, transfer, and execution-route states remain
`UNVERIFIED`; separate evidence and controls are required to advance any of
them.

## Inventory is not settlement

Pre-funded inventory is capital already held at a venue and can support a
venue-local trade without moving assets between venues. A settlement network
describes a possible asset/network dimension for settlement or a future
transfer assessment; it does not establish a transfer path, enabled deposits
or withdrawals, fee/latency, reachability, or route eligibility. This catalog
does not create CEX markets, symbols, or order books from networks. It is not
wired into trading or execution behavior.

Offline checks:

```powershell
python -m pytest -q tests/test_network_scope.py
python -m py_compile arb_bot/arbx/hybrid/networks.py tests/test_network_scope.py
```
