# Optional plugin catalog and enablement gates

Plugins are optional application extensions, not automatically connected services. The initial runtime
only supports read-only evidence requests with explicit provider/operation permissions. No provider is
enabled by default; secrets and write-capable tokens must not be passed to evidence providers.

## Candidate integrations

| Plugin / service | Potential Nu-Arb use | Default state | Review gate |
|---|---|---|---|
| GitHub | Repository issues, pull requests, CI status, release notes | Disabled | Read-only repository scope; redact secrets from logs |
| Vercel | Deployment metadata and build status | Disabled | Read-only project scope; deployment actions require a separate review |
| PostgreSQL | Durable app state and append-only audit records | Opt-in infrastructure | Migration, least-privilege role, backup/restore drill, and parity checks |
| PostGIS | Spatial queries only if a future product requirement has real geographic data | Not applicable by default | Do not install for market data or latency optimization |
| Metrics/observability provider | Health, error rates, latency histograms | Disabled | Read-only dashboard token; no configuration-write permission |

## Enablement checklist

1. Document the use case, data classes, retention, and failure behavior.
2. Define exact provider name, operations, and least-privilege scope.
3. Use a fake provider to test success, stale/invalid evidence, timeout, rate limit, exception, and malformed payload.
4. Ensure request/response logs omit credentials, tokens, and raw sensitive payloads. Store only status and payload hash where appropriate.
5. Enforce a bounded timeout and reject unpermitted operations before invoking the provider.
6. Persist audit metadata through a reviewed backend writer and test append-only behavior.
7. Enable one integration at a time after CI and human review; retain a simple disable switch.

## Explicit exclusions

This foundation does not enable automated financial decisions, exchange trading, signing, withdrawals,
or fund transfers. Plugins that offer write-capable or financial execution actions are outside the
read-only evidence interface and require a separate safety, security, and product review.
