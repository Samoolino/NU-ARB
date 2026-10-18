# Nu-Arb Plugin Integration Plan

## Purpose

Integrate external research, repository, security, and deployment tools as optional, permission-scoped adapters. Plugin availability does not imply certification of an exchange, validated profitability, or permission to execute orders.

## Integration sequence

### 0. CI and safety baseline
- Keep changes in reviewable pull requests; run compile, focused tests, full tests, migration tests, and container smoke tests.
- Keep live execution disabled in development, CI, and paper staging.
- Require a separate human-reviewed release decision before any future change to execution permissions.
- Treat external service output as untrusted input; validate schemas, timestamps, provenance, and failure states.

### 1. Read-only market and chain data
- Binance, Bybit, Gate Info, CoinGecko: optional public market snapshots and metadata.
- Blockscout and Etherscan: optional public chain, token, and contract lookups.
- Use a common typed provider interface, timeouts, retry budgets, response validation, and source timestamps.
- Do not submit orders, sign transactions, move funds, or request withdrawal permissions through these adapters.
- Mark missing, stale, contradictory, or rate-limited data as unavailable; never treat it as positive evidence.

### 2. Strategy research (non-executing)
- AlgoVault, TradingCursor, TraderSpy: ingest research signals only through an explicitly configured adapter.
- Preserve provider, timestamp, raw-response hash, normalized result, and uncertainty in the audit journal.
- Signals may inform a report or paper simulation but cannot directly call order-placement methods.
- Do not present signals or simulations as guaranteed profit or loss-free outcomes.

### 3. Security and network assurance
- FiraQuantumSafe: optional TLS/post-quantum transport posture evidence for supported endpoints.
- Proxyman and cryptography-research-skills: documented manual/CI-assisted diagnostics and threat-model review where the tool supports it.
- ethskills and TokenOS dApp Builder: design-time smart-contract and integration review only; no automatic transaction signing.
- Never collect private keys, seed phrases, or raw exchange secrets into diagnostics. Redact credentials and sensitive headers from logs.

### 4. Engineering, deployment, and durable work
- GitHub + agent-skills: issue/PR workflows, test-first changes, code review, security review, and evidence-linked approvals.
- Vercel: preview/deployment status and health checks; no production promotion until required gates pass.
- Cerebrium: optional isolated compute for non-sensitive batch research only after data residency, secrets, and egress controls are reviewed.
- ZzzOps: durable implementation goals and dependency tracking, with each goal linked to an issue/PR and verification evidence.

## Required architecture

- Define typed interfaces for each provider family; keep vendor SDKs behind adapters.
- Use explicit allowlists and least-privilege scopes. Default to disabled if credentials, configuration, or permissions are ambiguous.
- Apply bounded timeouts, rate limits, retries, circuit breakers, and schema validation.
- Persist provider name, request type, timestamp, status, latency, evidence references, and redacted error details.
- Use PostgreSQL as the canonical control-plane and execution-journal store once the adapter and migrations are implemented and tested.
- Provide deterministic fake providers for unit tests; CI must not require real credentials or make order/transaction calls.
- Feature flags must default off for optional providers and off for any execution-capable path.

## Release gates

1. CI compile and test suites pass on the exact reviewed commit.
2. PostgreSQL migrations apply cleanly to an empty database and are safe to re-run.
3. Docker Compose paper staging starts with healthy PostgreSQL, persistent journal storage, and live execution disabled.
4. Venue checks are read-only and produce per-venue timestamped REST/WebSocket/permission/latency evidence; no venue is certified from catalog membership alone.
5. Audit records redact secrets and are tested for failure and partial-data cases.
6. Security and code review approve the exact commit; deployment remains a separate gated action.

## Plugin inventory and status

The names below are proposed integration candidates, not assertions that a working connection or permission already exists: GitHub, agent-skills, AlgoVault, Binance, Blockscout, Bybit, Cerebrium, CoinGecko, cryptography-research-skills, ethskills, Etherscan, FiraQuantumSafe, Gate Info, Proxyman, TokenOS dApp Builder, TradingCursor, TraderSpy, Vercel, and ZzzOps.

Each provider needs an owner, documented purpose, permissions inventory, data-retention decision, test doubles, failure-mode tests, and an explicit enablement decision before implementation.
