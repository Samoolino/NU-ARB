# Advisory supervisor contract

## Purpose

`arbx.agentic.supervisor` aggregates already-produced specialist reports. It does not invoke an LLM, connect to an exchange, calculate authoritative PnL, or call an order API. It is an orchestration seam, not a trading engine.

## Report contract

Each report contains:

- `role`: one of `connectivity`, `market_data`, `opportunity`, `risk_review`, or `operator_summary`;
- `finding`: `INFO`, `WARN`, `REJECT`, or `UNKNOWN`;
- `observation_id`: identifier for the exact normalized observation being reviewed;
- `observed_at_ms`: integer timestamp in milliseconds;
- `summary`: bounded plain-text summary (maximum 500 characters); control characters are stripped before the report is returned.

The supervisor accepts at most 20 reports per call. Oversized collections, malformed mappings, duplicate required-role configuration, and invalid timestamps fail closed. Callers should still apply request timeouts and bounded concurrency before reports reach this function.

The aggregator rejects malformed reports, duplicate roles, mismatched observation IDs, stale/future timestamps, missing required roles, and any `REJECT` or `UNKNOWN` finding. Its successful status is `ADVISORY_ONLY`; it does not expose an execution or live-eligibility flag.

## Example

```python
from arbx.agentic.supervisor import aggregate_reports

assessment = aggregate_reports(
    reports,
    expected_observation_id="snapshot-123",
    now_ms=current_time_ms,
    max_age_ms=1500,
)
if assessment.status != "ADVISORY_ONLY":
    # Keep the existing engine's rejection/diagnostic path authoritative.
    log_agent_diagnostics(assessment.reasons)
```

The caller must obtain `current_time_ms` from its trusted clock and `expected_observation_id` from the engine-owned snapshot. Do not accept either value from an agent report.

## Integration rules

1. Build reports from sanitized, engine-owned observations; never pass credentials, signing material, or unrestricted clients to an agent.
2. Bound model calls, output size, retries, and concurrency in the caller. A timeout must be represented as missing/unknown evidence and must not be converted to `INFO`.
3. Keep engine permission, PnL, route, balance, depth, latency, and execution-state checks authoritative.
4. Agent reports cannot change thresholds, mark a venue live eligible, or place/cancel/amend orders.
5. Test this aggregation contract independently before connecting any model provider. Then use recorded observations and paper-mode replay; do not infer production readiness from unit tests alone.

## Verification status

The module and focused tests were committed to the advisory PR branch. They have not been executed in this workflow, and live exchange connectivity has not been tested. Run the repository's configured Python test suite and CI before merging.
