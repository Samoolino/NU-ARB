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
- SQL Expert: schema/query review, migration and data-quality analysis, and performance recommendations; generated SQL must be reviewed and tested against the repository's supported PostgreSQL version before use.
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

The names below are proposed integration candidates, not assertions that a working connection or permission already exists: GitHub, agent-skills, AlgoVault, Binance, Blockscout, Bybit, Cerebrium, CoinGecko, cryptography-research-skills, ethskills, Etherscan, FiraQuantumSafe, Gate Info, Proxyman, SQL Expert, TokenOS dApp Builder, TradingCursor, TraderSpy, Vercel, and ZzzOps.

Each provider needs an owner, documented purpose, permissions inventory, data-retention decision, test doubles, failure-mode tests, and an explicit enablement decision before implementation.

## Next implementation sequence (ordered deliverables)

This is the execution order for follow-up PRs. Keep each phase independently reviewable and do not combine optional provider wiring with changes to execution permissions.

### Phase A — establish the durable data and safety boundary
**Deliverables**
- Add a PostgreSQL driver and a migration runner compatible with the repository's current synchronous API/journal call sites.
- Implement and test repositories for users/sessions/credential metadata, opportunities, trades, and venue-certification evidence; preserve SQLite compatibility only where current deployments need it.
- Add Docker Compose for the control API and PostgreSQL with a health check, persistent database volume, non-root app process, and paper/read-only defaults.
- Add configuration validation that rejects ambiguous provider settings and keeps every optional integration disabled by default.
**Acceptance**
- Empty-database migration, repeat migration, rollback/failure handling, repository contract tests, and PostgreSQL-backed integration tests pass.
- No secret values appear in logs; CI never needs real credentials and cannot place orders.

### Phase B — normalize provider contracts and evidence journal
**Deliverables**
- Define typed interfaces for market snapshots, chain/contract lookups, research signals, deployment checks, and security diagnostics.
- Normalize status, source timestamp, observed-at timestamp, latency, provenance, schema version, and redacted error details.
- Add deterministic fake providers and stale/missing/conflicting-data tests.
**Acceptance**
- Invalid or stale responses fail closed to `unavailable`; they cannot be converted into a positive opportunity or venue certification.

### Phase C — read-only market and chain adapters
**Provider candidates**
- Market data: Binance public endpoints, Bybit public endpoints, Gate Info, CoinGecko.
- Chain data: Blockscout, Etherscan.
**Deliverables**
- One adapter per provider, with allowlisted endpoints, bounded timeouts/retries, rate-limit handling, schema validation, and source timestamps.
- Read-only diagnostics showing data freshness and provider health without asking for withdrawal or trading permissions.
**Acceptance**
- Contract tests and recorded/fake response tests pass; adapter failure does not halt the API or fabricate a quote.

### Phase D — research and signal adapters (report/paper only)
**Provider candidates**
- AlgoVault, TradingCursor, TraderSpy.
**Deliverables**
- Typed signal normalization with provider identity, timestamp, confidence/uncertainty fields where supplied, raw-response hash, and audit reference.
- Comparison/report endpoint and optional paper-simulation input.
**Acceptance**
- No signal adapter can access order placement, wallet signing, withdrawals, or live-execution interfaces. Reports explicitly state that estimates are uncertain and profits are not guaranteed.

### Phase E — security and smart-contract review helpers
**Provider candidates**
- FiraQuantumSafe, Proxyman, cryptography-research-skills, ethskills, TokenOS dApp Builder.
**Deliverables**
- Documented TLS/endpoint diagnostics where supported, sanitized network traces, threat-model checklists, and design-time contract/integration review artifacts.
- Secret redaction tests for authorization headers, API keys, cookies, wallet material, and request bodies.
**Acceptance**
- These helpers cannot export raw secrets, sign transactions, or make deployment decisions automatically. Unsupported capabilities are recorded as manual steps, not simulated as completed checks.

### Phase F — engineering, compute, and delivery workflows
**Provider candidates**
- GitHub and agent-skills; SQL Expert for reviewed schema/query analysis; Vercel; Cerebrium; ZzzOps.
**Deliverables**
- GitHub issue/PR automation tied to explicit allowlisted repositories and branches; test-first changes, review summaries, and evidence links.
- Vercel preview/health-status checks; production promotion remains a separate, human-approved action.
- ZzzOps goals mapped to repository issues/PRs and verification evidence.
- Cerebrium considered only for isolated, non-sensitive batch workloads after egress, retention, data-residency, and secret-handling review.
**Acceptance**
- No autonomous merge, production promotion, secret upload, or unreviewed code execution. All actions are scoped, auditable, and reversible where possible.

### Phase G — venue diagnostics and evidence-based certification
**Deliverables**
- Per-venue read-only REST/WebSocket checks with observed timestamps, latency, market-data freshness, supported capabilities, permission status where safely verifiable, and redacted failure reasons.
- Separate states for catalog-listed, public-data reachable, authenticated-read verified, WebSocket verified, and reviewed/certified.
**Acceptance**
- Catalog presence never means connectivity or certification. No order placement or fund movement is used as a connectivity test.

### Phase H — release gates
- Run focused tests after each phase, then full unit/integration suites, PostgreSQL migration tests, container smoke tests, dependency/security checks, and audit-redaction tests.
- Attach evidence to the exact reviewed commit and require human review before merge/deployment.
- Keep live execution disabled in development, CI, and paper staging. Any future execution feature requires a separate safety review and explicit authorization; no strategy or provider may promise a no-loss outcome.

### Cross-cutting plugin register
| Candidate | Initial permitted purpose | Initial prohibited actions |
|---|---|---|
| GitHub | Scoped repository reads, issues, PRs, CI evidence | Unreviewed merge or deployment |
| agent-skills | Engineering/test/review guidance | Treating generated output as verified without tests |
| AlgoVault | Research signal ingestion | Direct order invocation |
| Binance | Public market data | Trading/withdrawals |
| Blockscout | Public chain/token/contract reads | Signing or moving assets |
| Bybit | Public market data | Trading/withdrawals |
| Cerebrium | Reviewed isolated non-sensitive batch research | Secret-bearing jobs or uncontrolled egress |
| CoinGecko | Public asset/market metadata | Treating estimates as executable quotes |
| cryptography-research-skills | Security design/review | Automatic key handling or signing |
| ethskills | Smart-contract design/review | Automatic transaction signing |
| Etherscan | Public chain/contract reads | Signing or moving assets |
| FiraQuantumSafe | Supported TLS posture checks | Claiming unsupported checks passed |
| Gate Info | Public market data | Trading/withdrawals |
| Proxyman | Sanitized network diagnostics | Capturing or exporting raw secrets |
| SQL Expert | Reviewed schema, query, migration, and data-quality analysis | Unreviewed production DDL/DML, destructive changes, or direct production database access by default |
| TokenOS dApp Builder | Design-time dApp/integration artifacts | Automatic deployment or signing |
| TradingCursor | Research signals and reports | Direct order invocation |
| TraderSpy | Research signals and reports | Direct order invocation |
| Vercel | Preview/deployment status and health checks | Unreviewed production promotion |
| ZzzOps | Durable goals and dependency tracking | Marking work complete without evidence |

A provider is not considered integrated until its adapter, permission inventory, configuration/feature flag, audit fields, fake-provider tests, failure-mode tests, and documentation are merged. The inventory above describes intended boundaries, not confirmed connector availability.
