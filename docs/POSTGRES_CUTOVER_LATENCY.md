# PostgreSQL cutover, latency, and PostGIS decision

## Current implementation boundary

The PostgreSQL foundation is opt-in. The control API and trade journal still use SQLite-specific connections, SQL placeholders, schema initialization, and row access. Do not set PostgreSQL as the runtime default until repository parity and integration tests pass.

## Cutover procedure

1. Define typed repository contracts for users/sessions, encrypted credential metadata, journal trades, opportunities, venue certifications, provider evidence, and audit events.
2. Implement SQLite and PostgreSQL repositories behind those contracts. Keep encryption at the application boundary; store only ciphertext in the database.
3. Add contract tests that run unchanged against both repository implementations. Cover uniqueness, foreign keys, ordering, JSON payload validation, expiry, commits, and rollback behavior.
4. Add a disposable PostgreSQL CI service and run migrations plus repository integration tests against it. Include migration interruption/retry and concurrent-write tests.
5. Add Compose health checks, a persistent database volume, a least-privilege application role, and a separate migration role. Do not run schema migrations with the normal application role.
6. Benchmark synthetic, representative datasets on the same host and workload. Record p50/p95/p99 query and transaction latency, connection-acquisition time, error rate, and throughput. Include cold and warm runs.
7. Switch only through explicit configuration after parity tests, backup/restore rehearsal, and rollback documentation are reviewed. SQLite remains the default until that gate is satisfied.

## Latency measurement and tuning

- Measure application end-to-end latency separately from database server execution time and external exchange/network latency.
- Use EXPLAIN (ANALYZE, BUFFERS) on safe, representative read queries; note that ANALYZE executes the statement, so do not use it blindly with writes or side effects.
- Enable and review pg_stat_statements where deployment policy permits. Track high-total-time and high-mean-time normalized queries; avoid collecting credentials or raw sensitive payloads in logs.
- Add B-tree indexes based on actual filters, joins, ordering, and cardinality. Re-check plans after ANALYZE; avoid speculative indexes that add write amplification.
- Reuse bounded connections and set explicit acquisition/statement timeouts. Add a pooler only when measurements show connection churn or concurrency warrants it.
- Keep high-frequency exchange WebSocket ingestion and short-lived order-book state out of synchronous database round trips. Persist durable audit/journal records asynchronously only where ordering and durability requirements remain explicit.
- Never interpret a database benchmark as evidence of exchange connectivity, market-data freshness, execution quality, or profitability.

## PostGIS decision

PostGIS is a PostgreSQL extension for geospatial data. Its GiST, SP-GiST, and BRIN indexes accelerate suitable spatial predicates and spatially organized datasets. Nu-Arb's current core entities—venues, symbols, balances, opportunities, trades, credentials metadata, and provider evidence—do not present an established geospatial query requirement. Do not install or enable PostGIS in the core deployment unless a reviewed feature introduces a real spatial use case.

If such a use case appears, benchmark a spatial query with and without the appropriate spatial index on representative data. Use index-aware predicates such as ST_DWithin or ST_Intersects, and confirm the actual plan rather than assuming the index is used.

## Optional plugin sequence after database parity

1. Typed plugin contracts, permission scopes, evidence provenance, audit events, and deterministic fake-provider tests.
2. Read-only public market/reference data adapters (Binance, Bybit, Gate Info, CoinGecko).
3. Read-only chain explorer/indexer adapters (Blockscout, Etherscan), with network and freshness validation.
4. Research-only signals (AlgoVault, TradingCursor, TraderSpy) isolated from execution decisions and clearly labelled as unverified external evidence.
5. Security/developer tooling (FiraQuantumSafe, cryptography research, ethskills, TokenOS review helpers, GitHub/agent-skills, SQL Expert, Vercel, Cerebrium, ZzzOps). Limit database tools to reviewed schema/query work; no direct production mutations by default.

Plugin availability is not proof of a working adapter. Each integration needs typed inputs/outputs, minimum permissions, timeouts, redaction, stale/unavailable/error states, audit records, and fake-provider tests. CI must not require private credentials or invoke real-world financial actions.

## Safety and release gates

- No live orders, signing, transfers, or production credential use in database tests.
- No profit or no-loss guarantee.
- Production cutover requires human review, verified backup/restore, least-privilege roles, and a documented rollback.
- Keep all integrations read-only until separately reviewed and approved.
