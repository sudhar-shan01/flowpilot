# Phase 10A deployment threat assessment

This assessment covers the single-VM Google Compute Engine pilot deployment.
It does not claim enterprise high availability or exactly-once distributed
effects.

## Protected assets

- Lead identity, message, budget, analysis, and draft content
- Approval tokens and plaintext idempotency keys
- PostgreSQL and n8n persistent data
- OpenAI, SMTP, HubSpot, and Google Sheets credentials
- `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`, and the lead-ingress secret

## Primary exposure risks and controls

| Risk | Phase 10A control | Residual risk |
| --- | --- | --- |
| Untrusted callers trigger the lead workflow | HTTPS plus required `X-FlowPilot-Webhook-Secret`; internal verifier uses fixed-length SHA-256 digests and constant-time comparison | A trusted source can still be compromised; rotate the secret and investigate downstream state |
| n8n editor or FastAPI docs exposed publicly | Only Caddy publishes public ports; n8n and API bind to VM loopback for SSH tunneling | A user with VM access remains trusted |
| PostgreSQL exposed to the internet | No host port; database joins only the internal Docker backend network | Single-host compromise can reach container networks |
| Approval-link scanner causes a decision | Existing GET is display-only; explicit POST and atomic token checks remain required | Anyone who steals an unexpired URL may submit the decision |
| Approval token leaks through proxy logs or referrers | No Caddy access log; `Referrer-Policy: no-referrer`; restricted n8n retention | n8n error executions may contain request metadata and require restricted access |
| Oversized or abusive requests exhaust n8n | Caddy limits lead bodies to 64 KB and approval bodies to 16 KB; lead ingress requires a secret | No distributed rate limiter or WAF is included in the pilot architecture |
| Secrets enter source control or image layers | Production examples are blank; `.env.*` is ignored; credentials remain in n8n's encrypted store | Operators must protect the root-owned environment file and backups |
| Ambiguous external effects are replayed | Existing uncertain/recovery-required states and reconciliation runbook remain unchanged | Human investigation is still required |
| VM or disk failure loses state | Logical database backups, n8n/disk snapshots, and encryption-key escrow are required | One VM is still a single point of failure |

## Public route policy

The public origin serves only:

- `GET /health`
- `POST /webhook/flowpilot/lead` after shared-secret authorization
- `GET|POST /webhook/flowpilot/approval` using the existing approval-token model

All other routes return `404`. The n8n editor, API documentation, and direct API
lead/draft endpoints are available only through an authenticated SSH tunnel to
the VM loopback ports.
