# FlowPilot development history

FlowPilot was built in incremental phases so each reliability boundary could be
reviewed before the next external side effect was added. This page preserves
that history while the main README presents the repository as one product.

## Capability milestones

1. **AI analysis API:** validated lead input, structured AI output, provider
   abstraction, and sanitized failures.
2. **Workflow intake:** n8n webhook validation and deterministic priority
   routing.
3. **Persistence:** PostgreSQL became the durable source of lead state, followed
   by sequenced Google Sheets persistence.
4. **CRM synchronization:** exact-email HubSpot lookup with explicit create and
   update paths.
5. **Drafts and internal notification:** validated plain-text response drafts,
   `pending_approval` persistence, and internal SMTP notification.
6. **Human approval:** expiring single-use approve/reject links and atomic state
   transitions before an approved draft can be sent.
7. **Follow-up:** one fixed follow-up scheduled 72 hours after a confirmed
   initial send, with atomic claiming for concurrent workers.
8. **Reliability:** inbound idempotency, durable business stages, uncertain-email
   quarantine, stale-work reconciliation, transactional audit events, and
   privacy-safe operator views.
9. **Product packaging:** a Docker Compose stack, explicit ordered migrations,
   pinned dependency graphs, persistent service state, and reviewer-focused
   setup and demo documentation.
10. **Production readiness:** a deliberately small OCI single-VM deployment
    model, Caddy TLS boundary, authenticated machine ingress, private service
    networking, secret handling, and documented backup/restore operations.

## Design progression

The early API intentionally stayed small: FastAPI owns input and output
validation, while `AIService` isolates provider behavior. n8n then became the
workflow coordinator and PostgreSQL the source of durable ownership and state.
External systems were added only after their failure boundary could be named and
sanitized.

Email delivery drove the conservative recovery model. A network failure after
an SMTP attempt cannot prove whether the server accepted the message, so
FlowPilot records `uncertain` and blocks automatic resend. The same principle is
used for multi-step business work: once external side effects may have started,
a stale request becomes `recovery_required` instead of being replayed.

Phase 8 added observability without adding unsafe operator mutations. Database
triggers write audit events transactionally; the recovery workflow records only
counters; read-only views expose opaque references and technical state without
customer content, approval tokens, drafts, or plaintext idempotency keys.

Phase 9 changes delivery rather than business behavior. The API, workflows, and
database contracts remain the same, but a reviewer can run the core stack with
Docker alone. Migration execution is explicit and rerunnable, startup is gated
by health and migration completion, and n8n workflow/credential import remains
manual to avoid silently creating privileged external access.

Phase 10A prepares that same stack for a bounded cloud pilot without creating
cloud resources. Only the reverse proxy is public, trusted lead sources require
a shared ingress secret, administration stays behind an SSH or OCI Bastion
tunnel, and the single-VM durability and recovery limits are explicit.

## Detailed records

The main README retains configuration and operational notes for each capability.
The database migrations in `postgres/init/` and the deterministic tests provide
the executable history of schema and workflow behavior. The current operational
response process is documented in `docs/reliability-runbook.md`.
