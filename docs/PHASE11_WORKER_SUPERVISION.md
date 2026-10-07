# Phase 11 — Isolated read-only worker supervision

`arbx.worker_supervisor.WorkerSupervisor` provides an injectable async lifetime
manager for independent, long-running scanner and market-data workers. It
maintains process-local health snapshots, enforces per-attempt timeouts, retries
only timeout/explicitly transient errors, applies bounded exponential backoff,
and opens a per-worker circuit on permanent failure or exhausted retries.
One worker's failure does not cancel its siblings. A worker can report a
successful health observation through `WorkerContext.report_healthy()`.

Worker health contains only a constrained worker name, role, state, counts, and
an allow-listed error category. Logs use structured category/count fields and
never include exception messages, raw response values, or credential material.
The default classifier treats timeout and OS/connection errors as retryable;
other exceptions are permanent. Callers may inject a stricter classifier,
sleep function, logger, and worker factories for deterministic offline tests.

## Explicit execution boundary

Only `READ_ONLY_SCANNER` and `MARKET_DATA` roles can be registered. This
supervisor is not connected to `Hub`, `ExchangeWorker`, `ExecutionCoordinator`,
or the order path; production order behavior is unchanged. It must not be used
for order submission, cancellation, fills, unwind, or any task with possible
financial side effects. An unknown order state must never trigger a blind
submission retry or an execution-worker restart. Existing execution recovery
and reconciliation controls remain responsible for those cases.

The health snapshot is in-memory process state, not durable storage. Workers
must honor asyncio cancellation for bounded shutdown, and a caller should
register only idempotent, read-only work. This phase performs no live network
calls, does not connect workers to an exchange, and does not establish scanner
eligibility or venue health.

Focused offline verification:

```powershell
python -m pytest -q tests/test_worker_supervisor.py
python -m py_compile arb_bot/arbx/worker_supervisor.py tests/test_worker_supervisor.py
```
