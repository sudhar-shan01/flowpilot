# FlowPilot

FlowPilot is a portfolio-grade, AI-powered business workflow automation platform. The project is being built incrementally to demonstrate practical Python, API, AI, integration, reliability, and deployment skills without hiding the core ideas behind unnecessary infrastructure.

This repository currently contains **Phase 1: the AI Lead Analysis API**,
**Phase 2: n8n webhook intake and priority routing**, **Phase 3A: PostgreSQL
persistence**, **Phase 3B: Google Sheets persistence**, **Phase 4: HubSpot
CRM contact synchronization**, and **Phase 5: internal email notifications and
AI-generated response drafts**.

## Business problem

Inbound leads often arrive as unstructured messages. Someone must read each request, identify what the prospect needs, estimate its quality and urgency, and decide what should happen next. That manual triage becomes slow and inconsistent as lead volume grows.

FlowPilot turns a validated lead submission into a predictable, structured
analysis. Phase 2 adds a real workflow entry point that routes the result by
priority without duplicating AI logic outside the API. Phase 3A records each
successfully validated lead and analysis in PostgreSQL. Phase 3B appends that
same stored lead to Google Sheets. Phase 4 then synchronizes a HubSpot contact
by email. Phase 5 generates and persists a plain-text response draft for human
approval and sends the configured team recipient an internal notification
before priority routing.

## Current capabilities

- `GET /health` for API availability checks
- `POST /lead/analyze` for validated lead intake and structured AI analysis
- `POST /lead/draft` for validated, plain-text lead response drafts
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
- Google Sheets persistence using the PostgreSQL-generated timestamp
- Sanitized `PERSISTENCE_ERROR` webhook responses when either persistence step fails
- HubSpot contact lookup with explicit create and update paths keyed by email
- Sanitized `CRM_ERROR` responses when CRM synchronization fails
- PostgreSQL draft persistence with `pending_approval` status
- Internal SMTP notification through the `FlowPilot Email` n8n credential
- Sanitized `DRAFT_ERROR` and `EMAIL_ERROR` partial-success responses

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

Phases 2 through 5 extend this architecture without changing the public webhook
success contract:

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
                                           Google Sheets row
                                                    │ appended
                                                    ▼
                                    HubSpot contact lookup/sync
                                                    │ synchronized
                                                    ▼
                                      POST /lead/draft
                                                    │ validated
                                                    ▼
                                    Persist pending response draft
                                                    │ saved
                                                    ▼
                                      Internal email notification
                                                    │ sent
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
│   ├── test_google_sheets_persistence.py
│   ├── test_health.py
│   ├── test_hubspot_crm.py
│   ├── test_email_drafts_workflow.py
│   ├── test_lead_drafts.py
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
The branches only prepare different webhook messages; persistence is handled by
the Phase 3A and 3B nodes before routing.

### Prerequisites

- The Phase 1 API installed and configured as described above
- n8n 2.x; the committed export is verified with n8n `2.37.10`
- A valid `OPENAI_API_KEY` in FlowPilot's local `.env` for real AI analysis

### Import the workflow

1. Start n8n and open `http://localhost:5678`.
2. Open **Workflows**, choose **Import from File**, and select
   `n8n/flowpilot-lead-workflow.json`.
3. Review the **Analyze Lead with FlowPilot** HTTP Request node.
4. Configure the PostgreSQL, Google Sheets, and HubSpot nodes as described below.
5. Save and activate the workflow to register its production webhook.

The export is inactive by design so importing it cannot unexpectedly expose a
webhook. It contains reference metadata for credentials named
`FlowPilot PostgreSQL`, `FlowPilot Google Sheets`, and `FlowPilot HubSpot`, but
no credential payload, token, password, database address, or spreadsheet ID.
Create or select all three local credentials before activation as described
below.

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

## Phase 3B: Google Sheets persistence

Phase 3B extends only the successful Phase 3A path. After PostgreSQL returns the
stored row, n8n appends the same lead and analysis to Google Sheets, using
PostgreSQL's `created_at` value. The existing priority switch and public webhook
success response run only after both persistence steps succeed.

```text
Validated analysis
      │
      ▼
PostgreSQL insert
      │ success
      ▼
Google Sheets append
      │ success
      ▼
Priority routing → unchanged webhook response
```

### Prepare the Google Sheet

Create a spreadsheet and add these headers to row 1, from column A through K in
exactly this order:

```text
created_at
name
email
company
message
budget
category
priority
lead_score
short_summary
recommended_action
```

Each line above represents one separate header cell. Header spelling and case
must match because the n8n mappings use these names.

### Configure Google Sheets in n8n

1. Create an n8n **Google Sheets OAuth2 API** credential named
   `FlowPilot Google Sheets` and complete Google's authorization flow in n8n.
2. Open **Append Lead to Google Sheets** and select that local credential.
3. In **Document**, select the spreadsheet from the list. To supply a
   Spreadsheet ID directly, change the field to **By ID** and paste the ID from
   the Google Sheets URL.
4. In **Sheet**, enter the sheet/tab name. The committed workflow uses `Leads`
   as the non-personal default; change it if your tab has another name.
5. Confirm **Map Each Column Below** remains selected, then save and activate
   the workflow.

The node explicitly maps all eleven columns and does not construct rows through
string concatenation. The workflow export contains only n8n credential-reference
metadata. OAuth access tokens, refresh tokens, client secrets, service-account
keys, spreadsheet IDs, and Google account details must stay in n8n or the local
Google credential and must never be committed.

### Success and partial-failure behavior

On success, the caller still receives only `success`, `route`, `message`, and
`analysis`; database IDs, timestamps, and spreadsheet details are not exposed.

- If PostgreSQL fails, Google Sheets is bypassed and the existing sanitized
  `PERSISTENCE_ERROR` is returned.
- If PostgreSQL succeeds but Google Sheets fails, the PostgreSQL row remains and
  the caller receives HTTP `503`, `PERSISTENCE_ERROR`, with the generic message
  `Lead was analyzed but could not be fully persisted.`

Phase 3B intentionally provides no distributed transaction or rollback between
PostgreSQL and Google Sheets. It also adds no compensation, retries, or
deduplication. Those reliability features remain scoped to Phase 8.

### Manual verification

Start PostgreSQL, FlowPilot, and n8n; configure both n8n credentials and the
Google Sheets Document/Sheet fields; then activate the workflow and send the
Phase 2 example webhook request. Verify that PostgreSQL contains the new row,
the sheet contains a matching row with the same `created_at`, and the webhook
response retains the documented Phase 2 shape.

Automated tests require no Google account, credential, network access, or live
spreadsheet. A live append test requires a user-authorized local Google Sheets
credential.

## Phase 4: HubSpot CRM contact synchronization

Phase 4 adds a HubSpot Contact sync after both persistence steps succeed and
before priority routing. It manages Contacts only: no HubSpot Company, Deal,
pipeline, ticket, marketing, or email objects are created.

```text
PostgreSQL saved → Google Sheets appended → HubSpot contact search by email
                                                   │
                              existing contact ────┴──── missing contact
                                      │                         │
                                    update                    create
                                      └───────────┬─────────────┘
                                                  ▼
                                          Priority routing
```

### Configure the HubSpot credential

1. In n8n, create a **HubSpot OAuth2 API** credential named
   `FlowPilot HubSpot`.
2. Connect the intended HubSpot account and grant contact read/write access.
3. Select that credential in **Search HubSpot Contact by Email**,
   **Update HubSpot Contact**, and **Create HubSpot Contact**.
4. Save and activate the workflow only after all three nodes show the local
   credential as connected.

The contact flow uses HubSpot's supported CRM v3 search, create, and update
endpoints through n8n HTTP Request nodes with the predefined HubSpot credential.
The built-in HubSpot contact node exposes a combined upsert rather than the
explicit update/create paths required for this phase.

### Lookup and field mapping

The search uses the lead's validated `email` as an exact equality filter and
requests at most one contact. A match supplies the HubSpot contact ID only to the
internal update request; no match selects the create request. Repeated leads
with the same email therefore update the existing Contact instead of creating a
duplicate.

FlowPilot maps only standard Contact properties:

- `email` from the lead email
- `firstname` from the first component of the trimmed lead name
- `lastname` from the remaining name components, omitted when there are none
- `company` from the lead company name

No missing values are invented, no custom properties are created, and AI
analysis fields remain in PostgreSQL and Google Sheets.

### CRM failure and partial-success behavior

The existing persistence failure behavior remains unchanged:

- PostgreSQL failure bypasses both Google Sheets and HubSpot.
- Google Sheets failure bypasses HubSpot.
- A HubSpot search, create, update, authorization, or unexpected-response
  failure returns HTTP `503` with this sanitized body:

```json
{
  "success": false,
  "error": {
    "code": "CRM_ERROR",
    "message": "Lead was persisted but could not be synchronized with CRM."
  }
}
```

HubSpot response bodies, contact IDs, tokens, account details, API URLs, stack
traces, and credential data are never included in webhook responses. If HubSpot
fails, the PostgreSQL row and Google Sheets row remain; Phase 4 performs no
rollback. Retries, compensation, recovery workflows, and broader deduplication
remain scoped to Phase 8.

On success, the webhook response still contains only `success`, `route`,
`message`, and `analysis`. The HubSpot contact ID remains internal.

### Manual verification

Configure the existing PostgreSQL and Google Sheets steps, connect the local
`FlowPilot HubSpot` credential, activate the workflow, and send the Phase 2
example request. Confirm a HubSpot Contact is created with the mapped fields.
Send the same email again with an updated name or company and confirm the same
Contact is updated rather than duplicated, then verify the public webhook
response retains its existing shape.

Automated tests require no HubSpot account, credential, or network access. A
live sync requires a user-authorized HubSpot credential and is optional when one
is not already available locally. The workflow export contains only credential
reference metadata; OAuth tokens, private-app tokens, client secrets, portal
IDs, and other account data must remain in n8n and must never be committed.

## Phase 5: Email notifications and AI-generated response drafts

Phase 5 begins only after HubSpot synchronization succeeds. n8n sends the
validated lead and analysis to `POST /lead/draft`, persists the returned draft,
and sends an internal notification. The draft is never sent to the lead and is
not included in the public webhook response.

`POST /lead/draft` accepts this shape:

```json
{
  "lead": {
    "name": "John Doe",
    "email": "john@example.com",
    "company": "Acme Industries",
    "message": "We need help automating our inventory workflow.",
    "budget": 5000
  },
  "analysis": {
    "category": "workflow automation",
    "priority": "high",
    "lead_score": 88,
    "short_summary": "Acme needs inventory automation.",
    "recommended_action": "Schedule a discovery call."
  }
}
```

It returns only `subject` and `body`. Both are revalidated after the provider
call. Subjects must be a single line and no longer than 160 characters; bodies
are limited to 2,000 characters. Empty values, extra fields, malformed output,
and HTML-like content are rejected. The provider prompt forbids invented
pricing, discounts, timelines, promises, capabilities, recipients, names, and
signatures. Drafts remain plain text and require human approval in Phase 6.

### Draft persistence

`postgres/init/002_add_lead_drafts.sql` upgrades existing `leads` tables by
adding nullable `draft_subject`, `draft_body`, and `draft_status` columns. The
workflow updates the row created earlier in the same execution using its
internal database ID and parameterized SQL. A valid draft is stored with
`draft_status = 'pending_approval'` before any email node runs. The migration is
additive and uses `IF NOT EXISTS`, so existing lead rows remain valid.

New PostgreSQL volumes apply both init files automatically. For an existing
FlowPilot volume, apply the additive migration once without recreating it:

```powershell
docker compose -f compose.postgres.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/002_add_lead_drafts.sql'
```

### Configure internal email

1. Create an n8n SMTP credential named `FlowPilot Email` and select it in
   **Send Internal Notification**.
2. Set `FLOWPILOT_INTERNAL_EMAIL_TO` to the trusted team recipient in n8n's
   local environment.
3. Set `FLOWPILOT_INTERNAL_EMAIL_FROM` to the sender address allowed by the SMTP
   account.
4. Restart n8n after changing its environment, then activate the workflow.

The recipient and sender are local configuration and must never be committed.
The model cannot provide or override either address. The notification contains
the lead summary, priority, score, recommended action, and pending draft. The
email is plain text and is sent only to the configured internal recipient.

### Failure and partial-success behavior

The original failure order remains intact. PostgreSQL failure bypasses Sheets,
CRM, drafting, and email. Sheets failure bypasses CRM, drafting, and email.
HubSpot failure bypasses drafting and email. Draft generation, validation, or
persistence failure returns HTTP `503` with sanitized `DRAFT_ERROR`. Missing or
invalid recipient configuration and SMTP failures return HTTP `503` with
sanitized `EMAIL_ERROR`.

Earlier successful PostgreSQL, Google Sheets, and HubSpot work is not rolled
back. No provider response, draft, recipient, SMTP detail, database ID, HubSpot
ID, or credential detail is exposed by the error response. On complete success,
the webhook still returns only `success`, `route`, `message`, and `analysis`.

### Manual verification

Apply the PostgreSQL migrations, start FlowPilot and n8n, configure the existing
Phase 3 and Phase 4 credentials, add the local `FlowPilot Email` credential and
the two email environment variables, then submit the documented webhook
request. Confirm the matching `leads` row contains the draft with
`pending_approval`, the internal mailbox receives one plain-text notification,
and the lead receives no email. Live SMTP verification is optional when no
credential is available; all automated tests remain network-free and
credential-free.

## Run tests

```powershell
python -m pytest
```

Tests replace the provider through FastAPI dependency overrides. They make no network calls and require no API key.

## Roadmap

- **Phase 1 (complete):** AI Lead Analysis API
- **Phase 2 (complete):** n8n webhook integration
- **Phase 3A (complete):** PostgreSQL persistence
- **Phase 3B (complete):** Google Sheets persistence
- **Phase 4 (complete):** HubSpot CRM contact synchronization
- **Phase 5 (complete):** Email notifications and AI-generated draft responses
- **Phase 6 (pending):** Human approval workflow
- **Phase 7 (pending):** Automated follow-ups
- **Phase 8 (pending):** Retries, deduplication, observability, and workflow reliability
- **Phase 9 (pending):** Docker deployment
- **Phase 10 (pending):** Cloud deployment

Development stops at Phase 5 until it has been reviewed and approved.
