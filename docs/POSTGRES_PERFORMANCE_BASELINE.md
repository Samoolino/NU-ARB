# PostgreSQL performance baseline

Do not add indexes, tune pooling, or change transaction boundaries before collecting a baseline
from representative non-production data. Record hardware, PostgreSQL version, dataset size, concurrency,
and exact test command.

## Required measurements
- Connection acquisition/release latency.
- Common API read-query latency.
- Representative read/write transaction duration.
- Migration duration and row counts.
- Error and timeout rates under concurrency.

Record p50, p95, and p99 with sample count. Use a monotonic clock, warm-up phase, fixed data, and
at least 1,000 measured operations per scenario where practical. Keep SQLite/PostgreSQL runs separate
but equivalent in data and operations. Never load-test production.

## Diagnostics
- EXPLAIN (ANALYZE, BUFFERS) executes the query; use controlled test data only.
- Enable pg_stat_statements only after operational review and record its reset window.
- Compare plans, rows scanned, buffer reads, and latency distributions before/after changes.
- Consider pooling only after measuring connection setup and concurrency.

## Results template
| Backend / scenario | Samples | p50 ms | p95 ms | p99 ms | Error rate | Dataset / concurrency |
|---|---:|---:|---:|---:|---:|---|
| SQLite read | not measured | — | — | — | — | record environment |
| PostgreSQL read | not measured | — | — | — | — | record environment |
| SQLite transaction | not measured | — | — | — | — | record environment |
| PostgreSQL transaction | not measured | — | — | — | — | record environment |

No performance values are claimed until the benchmark has been run in the target environment.
