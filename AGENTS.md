# NU-ARB Codex instructions

These instructions apply to work in this repository, including cloud development and documentation.

## Project context

- Canonical repository: https://github.com/Samoolino/NU-ARB (default branch: main).
- Documented dashboard: https://nu-arb.vercel.app. Verify current deployment health and project ownership before claiming a cloud connection or successful release.
- Read README.md and .github/agents/Enter.agent.md before implementation; follow their engineering, verification, credential and trading safeguards.
- Vercel serves the dashboard and Node API routes from the repository root. Railway hosts the durable Python control service, configured by railway.json and Dockerfile.control. Confirm actual account-level resource IDs and configuration through authorized access; configuration files alone do not prove deployment.

## Continuous development

Carry the user's requested NU-ARB work through implementation, relevant verification and reviewable delivery. Use existing issues, pull requests and project documentation to select bounded improvements when continuing an authorized development task. Preserve uncommitted work and avoid duplicating active changes.

Keep prompts, plans and project documentation grounded in the current repository and verified cloud state. Record completed work, remaining work, actual check results and exact blockers in relevant project documentation so later Codex sessions can continue. These instructions apply to this repository; they do not change unrelated chats or global Codex settings.

For each cloud change, identify the target service and environment, check the relevant existing configuration, implement the smallest coherent change, run applicable tests and verify the resulting deployment when authorized access permits. Use codex/ branches and reviewable pull requests. Never report a check, connection, release or running automation that has not been confirmed.

Recurring development requires a separately enabled Codex automation. Repository instructions do not schedule work or grant new cloud access. If an automation is rejected or disabled, report that state and do not recreate it without renewed authorization.

## Safeguards

Preserve all existing risk gates, paper/live separation, credential encryption, journal and emergency-stop behavior. Development authorization does not authorize live orders, enabling live trading, weakening controls or new paid infrastructure. Keep secrets out of prompts, logs, documentation and source control. Use existing authenticated integrations without requesting credentials in chat.
