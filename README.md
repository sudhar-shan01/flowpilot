# FlowPilot

FlowPilot is a portfolio-grade, AI-powered business workflow automation platform. The project is being built incrementally to demonstrate practical Python, API, AI, integration, reliability, and deployment skills without hiding the core ideas behind unnecessary infrastructure.

This repository currently contains **Phase 1: the AI Lead Analysis API**,
**Phase 2: n8n webhook intake and priority routing**, and **Phase 3A:
PostgreSQL persistence**. Google Sheets persistence remains pending for Phase
3B.

## Business problem

Inbound leads often arrive as unstructured messages. Someone must read each request, identify what the prospect needs, estimate its quality and urgency, and decide what should happen next. That manual triage becomes slow and inconsistent as lead volume grows.

FlowPilot turns a validated lead submission into a predictable, structured
analysis. Phase 2 adds a real workflow entry point that routes the result by
priority without duplicating AI logic outside the API. Phase 3A records each
successfully validated lead and analysis in PostgreSQL before routing it.

## Current capabilities

- `GET /health` for API availability checks
- `POST /lead/analyze` for validated lead intake and structured AI analysis
- Category, priority, score, summary, and recommended next action
- Provider-independent AI service boundary
- OpenAI-compatible JSON Schema structured output
- Controlled responses for missing credentials, timeouts, provider failures, and malformed model output
- Automated, network-free endpoint and failure-path tests
- Interactive API documentation at `/docs`
- Importable n8n lead-intake workflow at `n8n/flowpilot-lead-workflow.json`
- High, medium, and low priority routing with clean webhook responses
- Sanitized n8n responses for validation, availability, and malformed-result errors
- PostgreSQL persistence for validated lead analyses
- Version-controlled database schema with range and enum-like constraints
- Sanitized `PERSISTENCE_ERROR` webhook responses when a lead cannot be saved

## Architecture

```text
HTTP request
    │
    ▼
FastAPI route ── validates input with Pydantic
    │
    ▼
AIService ── stable application interface and output validation
    │
    ▼
AIProvider ── provider-specific request (OpenAI-compatible in Phase 1)
```

The route never calls an LLM SDK or endpoint directly. `AIService` accepts any implementation of `AIProvider`, which keeps provider replacement and test mocking small. Every provider response is treated as untrusted and revalidated as `LeadAnalysis` before it leaves the application.

The health endpoint intentionally does not depend on AI credentials or provider availability.

Phases 2 and 3A extend this architecture without changing the Phase 1 API:

```text
Webhook caller
    │
    ▼
n8n Webhook → Prepare Lead → POST /lead/analyze → Validate result
                                                    │ valid only
                                                    ▼
                                         PostgreSQL `leads`
                                                    │ saved
                                                    ▼
                                      Priority switch (high/medium/low)
                                                    │
                                                    ▼
                                          Clean webhook response
```

## Project structure

```text
flowpilot/
├── app/
│   ├── api/
│   │   ├── dependencies.py
│   │   └── routes/
│   │       ├── health.py
│   │       └── leads.py
│   ├── core/
│   │   └── config.py
│   ├── models/
│   │   └── lead.py
│   ├── services/
│   │   └── ai_service.py
│   └── main.py
├── tests/
│   ├── conftest.py
│   ├── e2e_flowpilot_app.py
│   ├── test_health.py
│   ├── test_leads.py
│   ├── test_n8n_workflow.py
│   └── test_postgres_persistence.py
├── n8n/
│   └── flowpilot-lead-workflow.json
├── postgres/
│   └── init/
│       └── 001_create_leads.sql
├── compose.postgres.yml
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Installation

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.

## Environment configuration

Copy the example configuration and add your API key:

```powershell
Copy-Item .env.example .env
```

```dotenv
FLOWPILOT_AI_PROVIDER=openai
OPENAI_API_KEY=your-api-key
FLOWPILOT_OPENAI_MODEL=gpt-4o-mini
FLOWPILOT_OPENAI_BASE_URL=https://api.openai.com/v1
FLOWPILOT_AI_TIMEOUT_SECONDS=20
FLOWPILOT_LOG_LEVEL=INFO
```

`.env` is ignored by Git. Never commit a real key. The API can start and report healthy without a key, but `/lead/analyze` returns HTTP `503` until one is configured.

## Run the API

```powershell
python -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for Swagger UI.

## Example request

```bash
curl -X POST http://127.0.0.1:8000/lead/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "name": "John Doe",
    "email": "john@example.com",
    "company": "Acme Industries",
    "message": "We need an automated inventory management solution for our 25-person company.",
    "budget": 5000
  }'
```

## Example response

The exact analysis varies by model, but it always follows this schema:

```json
{
  "category": "workflow automation",
  "priority": "high",
  "lead_score": 88,
  "short_summary": "Acme needs inventory automation for a 25-person team.",
  "recommended_action": "Schedule a discovery call to map inventory workflows."
}
```

Expected service errors use generic messages and do not expose stack traces or provider response bodies. Lead names, email addresses, companies, and messages are not written to application logs.

## Phase 2: n8n webhook integration

Phase 2 adds an n8n workflow that accepts an incoming lead, maps only the five
fields required by `LeadRequest`, calls the existing FlowPilot API, validates the
structured result, and routes it through explicit high, medium, or low branches.
The branches only prepare different webhook messages; they do not send email or
write to another system.

### Prerequisites

- The Phase 1 API installed and configured as described above
- n8n 2.x; the committed export is verified with n8n `2.37.10`
- A valid `OPENAI_API_KEY` in FlowPilot's local `.env` for real AI analysis

### Import the workflow

1. Start n8n and open `http://localhost:5678`.
2. Open **Workflows**, choose **Import from File**, and select
   `n8n/flowpilot-lead-workflow.json`.
3. Review the **Analyze Lead with FlowPilot** HTTP Request node.
4. Save and activate the workflow to register its production webhook.

The export is inactive by design so importing it cannot unexpectedly expose a
webhook. It references an n8n credential named `FlowPilot PostgreSQL`, but no
database address, username, or password is included. Create or select that
credential before activation as described in Phase 3A below.

### Start FlowPilot

From the repository root, with the Python virtual environment active:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Confirm `http://127.0.0.1:8000/health` returns `{"status":"healthy"}` before
starting n8n.

### Start and configure n8n

If n8n runs directly on the same machine as FlowPilot:

```powershell
$env:FLOWPILOT_API_URL = "http://127.0.0.1:8000"
$env:N8N_BLOCK_ENV_ACCESS_IN_NODE = "false"
npx n8n@2.37.10 start
```

If n8n runs in Docker Desktop while FlowPilot runs on the host:

```powershell
docker run --rm -it --name flowpilot-n8n `
  -p 5678:5678 `
  -e FLOWPILOT_API_URL=http://host.docker.internal:8000 `
  -e N8N_BLOCK_ENV_ACCESS_IN_NODE=false `
  -v n8n_data:/home/node/.n8n `
  docker.n8n.io/n8nio/n8n:2.37.10
```

`FLOWPILOT_API_URL` is the API base URL without `/lead/analyze`. When the
variable is absent, the workflow uses
`http://host.docker.internal:8000` as its Docker Desktop-friendly fallback.
Current n8n releases block `$env` expressions by default, so the examples
explicitly allow them. Keep secrets out of the n8n process environment when
using this setting; this workflow only reads the non-secret API base URL.

### Webhook URLs

- Production, after activation:
  `http://localhost:5678/webhook/flowpilot/lead`
- Test, while **Listen for test event** is active in the editor:
  `http://localhost:5678/webhook-test/flowpilot/lead`

Example production request:

```bash
curl -X POST http://localhost:5678/webhook/flowpilot/lead \
  -H "Content-Type: application/json" \
  -d '{
    "name": "John Doe",
    "email": "john@example.com",
    "company": "Acme Industries",
    "message": "We need inventory automation for our company.",
    "budget": 5000
  }'
```

A successful high-priority response has this shape:

```json
{
  "success": true,
  "route": "high",
  "message": "High-priority lead received. Schedule a discovery call promptly.",
  "analysis": {
    "category": "workflow automation",
    "priority": "high",
    "lead_score": 88,
    "short_summary": "Company needs inventory automation.",
    "recommended_action": "Schedule a discovery call."
  }
}
```

The medium and low branches return the same structure with their matching
`route` and branch message. Error responses intentionally omit internal details:

- HTTP `422`, `VALIDATION_ERROR`: the incoming lead failed Phase 1 validation
- HTTP `503`, `FLOWPILOT_UNAVAILABLE`: n8n could not reach the API
- HTTP `503`, `FLOWPILOT_ERROR`: the API reported that analysis is unavailable
- HTTP `502`, `INVALID_FLOWPILOT_RESPONSE`: the API returned an unexpected body

### Troubleshooting networking

- **n8n and FlowPilot both run on the host:** set
  `FLOWPILOT_API_URL=http://127.0.0.1:8000`.
- **n8n runs in Docker Desktop and FlowPilot runs on the host:** use
  `http://host.docker.internal:8000`. `localhost` inside the n8n container points
  back to that container, not to FlowPilot.
- **Linux Docker Engine:** add an appropriate host-gateway mapping or set
  `FLOWPILOT_API_URL` to an address the container can reach.
- **FlowPilot returns `503`:** confirm `OPENAI_API_KEY` is present in FlowPilot's
  `.env`, then restart the API.
- **n8n blocks `$env` access:** set `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` and
  restart n8n. On a hardened shared instance, leave the block enabled and set a
  non-secret local URL directly in the HTTP Request node instead.

## Phase 3A: PostgreSQL persistence

Phase 3A extends the existing n8n workflow with a PostgreSQL insert. The
original lead fields are carried across the FlowPilot HTTP request and combined
with the validated analysis. Only the workflow's valid-analysis path reaches
PostgreSQL; API and validation errors go directly to the existing clean error
response. Priority routing occurs only after the insert succeeds.

### Start local PostgreSQL

Docker Desktop with Docker Compose is the recommended local setup. Add a strong
local-only password to `.env`; the example deliberately leaves it blank:

```dotenv
POSTGRES_DB=flowpilot
POSTGRES_USER=flowpilot
POSTGRES_PASSWORD=replace-with-a-local-password
POSTGRES_PORT=5432
```

Then start only the database service from the repository root:

```powershell
docker compose -f compose.postgres.yml up -d
docker compose -f compose.postgres.yml ps
```

The first startup runs `postgres/init/001_create_leads.sql`. PostgreSQL stores
`TIMESTAMPTZ` values as UTC instants, and the local container is explicitly set
to UTC. The named Docker volume preserves data across ordinary container
restarts. The application itself is not Dockerized.

If the named volume already existed before the schema file was added, init
scripts will not run again. Apply the SQL file manually or recreate only this
development database volume after backing up any data you need.

### Configure the n8n PostgreSQL credential

In n8n, create a **Postgres** credential named `FlowPilot PostgreSQL`, then
select it in the **Persist Lead in PostgreSQL** node. Use the values from your
local `.env`:

- Database: `flowpilot` (or `POSTGRES_DB`)
- User: `flowpilot` (or `POSTGRES_USER`)
- Password: the local `POSTGRES_PASSWORD`
- Port: `5432` (or `POSTGRES_PORT`)
- SSL: disabled for this local-only database
- Host when n8n runs on the host: `127.0.0.1`
- Host when n8n runs in Docker Desktop: `host.docker.internal`

n8n encrypts credential values in its own credential store. Do not paste the
password into the workflow node or commit it to this repository. After choosing
the credential, save and activate the workflow.

The PostgreSQL node uses `$1` through `$10` query placeholders and n8n's Query
Parameters option. Lead values are never concatenated into SQL.

### Database schema

The `leads` table contains an identity `BIGINT` primary key, a defaulted
`TIMESTAMPTZ` creation time, the five original lead fields, and all five
analysis fields. PostgreSQL constraints permit only `high`, `medium`, or `low`
priority and scores from 0 through 100. Budget uses `NUMERIC(14, 2)` and follows
the API's accepted range.

Inspect the schema and saved rows with:

```powershell
docker compose -f compose.postgres.yml exec postgres `
  psql -U flowpilot -d flowpilot -c "\d leads"

docker compose -f compose.postgres.yml exec postgres `
  psql -U flowpilot -d flowpilot -c "SELECT id, created_at, email, priority, lead_score FROM leads ORDER BY id DESC;"
```

Submit the same webhook request documented in Phase 2. A successful response
keeps the Phase 2 response contract unchanged. If PostgreSQL is unavailable or
rejects the insert, the caller receives HTTP `503` with a sanitized body:

```json
{
  "success": false,
  "error": {
    "code": "PERSISTENCE_ERROR",
    "message": "Lead was analyzed but could not be saved."
  }
}
```

SQL error text, database addresses, credentials, stack traces, and other
internal details are not returned to the webhook caller.

### Phase 3A verification

Run the normal suite without a live database:

```powershell
python -m pytest
```

For a manual integration check, start PostgreSQL, FlowPilot, and n8n; configure
the n8n Postgres credential; activate the workflow; send the Phase 2 curl
request; and query the newest row as shown above. Stop PostgreSQL with:

```powershell
docker compose -f compose.postgres.yml down
```

## Run tests

```powershell
python -m pytest
```

Tests replace the provider through FastAPI dependency overrides. They make no network calls and require no API key.

## Roadmap

- **Phase 1 (complete):** AI Lead Analysis API
- **Phase 2 (complete):** n8n webhook integration
- **Phase 3A (complete):** PostgreSQL persistence
- **Phase 3B (pending):** Google Sheets persistence
- **Phase 4:** CRM integration
- **Phase 5:** Email notifications and AI-generated draft responses
- **Phase 6:** Human approval workflow
- **Phase 7:** Automated follow-ups
- **Phase 8:** Retries, deduplication, observability, and workflow reliability
- **Phase 9:** Docker deployment
- **Phase 10:** Cloud deployment

Development stops at Phase 3A until it has been reviewed and approved.
