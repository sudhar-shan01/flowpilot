# FlowPilot

FlowPilot is a portfolio-grade, AI-powered business workflow automation platform. The project is being built incrementally to demonstrate practical Python, API, AI, integration, reliability, and deployment skills without hiding the core ideas behind unnecessary infrastructure.

This repository currently contains **Phase 1 only: the AI Lead Analysis API**.

## Business problem

Inbound leads often arrive as unstructured messages. Someone must read each request, identify what the prospect needs, estimate its quality and urgency, and decide what should happen next. That manual triage becomes slow and inconsistent as lead volume grows.

FlowPilot Phase 1 turns a validated lead submission into a predictable, structured analysis that downstream automation can consume later.

## Current capabilities

- `GET /health` for API availability checks
- `POST /lead/analyze` for validated lead intake and structured AI analysis
- Category, priority, score, summary, and recommended next action
- Provider-independent AI service boundary
- OpenAI-compatible JSON Schema structured output
- Controlled responses for missing credentials, timeouts, provider failures, and malformed model output
- Automated, network-free endpoint and failure-path tests
- Interactive API documentation at `/docs`

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
│   ├── test_health.py
│   └── test_leads.py
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

## Run tests

```powershell
python -m pytest
```

Tests replace the provider through FastAPI dependency overrides. They make no network calls and require no API key.

## Roadmap

- **Phase 1:** AI Lead Analysis API
- **Phase 2:** n8n webhook integration
- **Phase 3:** Google Sheets and PostgreSQL persistence
- **Phase 4:** CRM integration
- **Phase 5:** Email notifications and AI-generated draft responses
- **Phase 6:** Human approval workflow
- **Phase 7:** Automated follow-ups
- **Phase 8:** Retries, deduplication, observability, and workflow reliability
- **Phase 9:** Docker deployment
- **Phase 10:** Cloud deployment

Development stops at Phase 1 until it has been reviewed and approved.
