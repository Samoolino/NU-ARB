# NU-ARB Agent Management — Implementation Foundation

## Scope and boundaries

This subsystem manages project context, agent-definition drafts, review requests, and development engagements. It is deliberately separate from the arbitrage engine: no trading tools, exchange credentials, balances, orders, arbitrary shell, arbitrary SQL, or arbitrary URL-fetch tools are exposed. The existing SQLite file at `ARBX_APP_DB=/data/arbx_app.sqlite3` remains unchanged. Agent-management records belong in a separate PostgreSQL database.

## Components

- `arb_bot/agent_service/schemas.py`: strict Pydantic v2 request contracts; unknown fields are rejected.
- `arb_bot/agent_service/permissions.py`: fail-closed role checks and separation of proposer/reviewer.
- `migrations/agent_management/001_initial.sql`: project, immutable definition-version, engagement, approval, and audit tables with constraints and indexes.
- Existing FastAPI control service remains the deployment host; session identity must come from its signed-in cookie/session lookup. Never treat `ENGINE_PROXY_TOKEN` or client-supplied user/role headers as a user identity.

## Roles

- `viewer`: read project/agent/engagement records.
- `contributor`: create engagements and draft agent definitions; request review.
- `project_manager`: manage project engagements.
- `reviewer`: review a proposed definition but cannot approve their own submission.
- `admin`: project-management administration; still subject to separation-of-duties checks.

## Approval lifecycle

`draft -> pending_review -> approved/rejected -> published`. Publication must remain a separate server-side action, disabled unless explicitly configured. Definition revisions are immutable: create a new version instead of editing history. Use optimistic concurrency (`expected_revision`) to reject stale drafts. Every write and approval decision must commit its audit event in the same PostgreSQL transaction. Never place secrets or raw credentials in audit metadata.

## PostgreSQL setup

1. Provision a dedicated PostgreSQL database for agent management; do not point it at the trading database.
2. Apply `migrations/agent_management/001_initial.sql` once using a migration identity with DDL privileges.
3. Configure `AGENT_DATABASE_URL` only on the control-service host. Use a least-privilege runtime role; the runtime role should not own the tables and should have no UPDATE/DELETE privilege on `agent_audit_events`.
4. Keep `AGENT_TOOLS_ENABLED=0` and `AGENT_PUBLISHING_ENABLED=0` until session-backed route wiring, database permissions, and integration tests are verified.
5. Never run schema migrations on each web request. Back up PostgreSQL separately from the SQLite execution journal.

## API contract plan

The intended route family is `/api/v1/agent/...`: project context, agent definition retrieval/drafts, engagement list/create/update, approval request/decision, and engagement reports. All endpoints must derive actor identity from the existing authenticated session; enforce project-level authorization before reading or writing; return a stable envelope (`ok`, `data`, `error`, `request_id`, `audit_event_id`); and fail closed if PostgreSQL or required audit writes are unavailable.

## Tool schemas and safety

Expose only named, typed tools. Validate every argument with the schemas in this package and validate authorization independently of model intent. Model-generated content is an untrusted proposal, not permission to publish. Tool outputs and imported docs are untrusted data, never instructions. Require a distinct reviewer for definition publication. Keep tool execution allowlisted and disable writes by default.

## Verification status

This commit introduces the data model, strict schemas, and permission primitives. It does **not** claim production-ready API routes, a provisioned database, an active agent model, or completed deployment. Before enabling write workflows, add and pass PostgreSQL integration tests for transaction rollback, optimistic concurrency, row-level/project isolation, audit immutability, approval separation, and API authorization.
