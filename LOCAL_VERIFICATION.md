# Local control-plane bypass (paper verification only)

This profile runs the Python FastAPI control service locally, without Railway. It binds only to loopback (127.0.0.1) and explicitly sets ARBX_LIVE_TRADING_ENABLED=0. It is intended to verify the local API, account/credential validation flow, and feed diagnostics before any hosted integration is considered. It does not authorize or submit live orders.

## Prerequisites

- Docker Engine + Docker Compose v2.
- Python 3.12 and the repository dependencies for CLI self-tests.
- Node.js 20+ if using the local frontend/Vercel development server.

## Start the isolated control API

PowerShell:

~~~powershell
Copy-Item .env.local.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "import secrets; print(secrets.token_hex(32))"
# Put the first output in ENGINE_PROXY_TOKEN and the second in CREDENTIAL_ENCRYPTION_KEY in .env.
docker compose -f compose.local.yml up --build -d
docker compose -f compose.local.yml ps
docker compose -f compose.local.yml logs --tail 100 control
Invoke-WebRequest http://127.0.0.1:8000/healthz
~~~

Do not leave the example placeholder values in .env. The encryption key must remain stable for any locally stored credentials. Do not reuse production keys or production credentials in this local test.

Bash/WSL alternative:

~~~bash
cp .env.local.example .env
python -c 'import secrets; print(secrets.token_urlsafe(48))'
python -c 'import secrets; print(secrets.token_hex(32))'
# Put the first output in ENGINE_PROXY_TOKEN and the second in CREDENTIAL_ENCRYPTION_KEY in .env.
docker compose -f compose.local.yml up --build -d
docker compose -f compose.local.yml ps
docker compose -f compose.local.yml logs --tail 100 control
curl -fsS http://127.0.0.1:8000/healthz
~~~

The compose file uses the existing Dockerfile.control, persistent local volume, and health endpoint. APP_SECURE_COOKIE=0 is acceptable only for this loopback-only HTTP test. Do not publish port 8000 to a LAN or the internet.

## Verify the Python engine and adapters separately

From the repository root:

~~~bash
python -m venv .venv
# Windows: .venv\\Scripts\\Activate.ps1
# WSL: source .venv/bin/activate
python -m pip install -r arb_bot/requirements.txt
python arb_bot/run.py selftest
~~~

The self-test is the offline baseline. For market-data diagnostics, set BOT_MODE=paper and use public REST/WebSocket feeds. The exchange support matrix in EXCHANGE_SUPPORT.md distinguishes adapter availability from actual REST authentication, private-stream verification, permission evidence, and execution eligibility. A registry entry is not a verified connection. Never paste API secrets into logs, shell history, issues, or chat.

For a local dashboard, run the frontend's supported development server with ARBX_CONTROL_API_URL=http://127.0.0.1:8000 and the same locally generated ENGINE_PROXY_TOKEN in the local server environment only. Do not configure a deployed Vercel service to use a developer's localhost address; Vercel server-side functions cannot reach your workstation's loopback.

## What constitutes a pass

- /healthz returns HTTP 200.
- Engine offline self-test passes.
- Each chosen venue has separate recorded results for public REST, public WebSocket, authenticated REST/balance, and private WebSocket (where supported).
- Feed timestamps are fresh and comparable; reconnects, rate limits, gaps, and stale books are visible as failures rather than silently accepted.
- Candidate rows include the venue, exact market symbol, both bid/ask prices, observation timestamps, visible depth/notional, fee assumptions, estimated slippage, network/contract evidence where relevant, and net estimated USD edge.
- All order submission and withdrawal/transfer paths remain disabled. Treat the $0.80 filter as a paper-signal threshold, not a promise of realizable profit.

## Stop and clean up

~~~bash
docker compose -f compose.local.yml down
~~~

This preserves the named volume. To delete the local database and locally stored credentials too, use docker compose -f compose.local.yml down -v only when you intentionally want to erase that local test data.
