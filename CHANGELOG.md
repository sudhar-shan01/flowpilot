# Changelog

## Unreleased

### Major development milestones

- Added the final synthetic end-to-end certification gate, including complete
  PostgreSQL lifecycle, approval race, migration-lock, indexed-query, burst,
  Docker restart/persistence, workflow import, and security evidence.
- Hardened public lead validation so prohibited extra fields cannot be dropped
  before FastAPI validation, and require confirmed recipient acceptance before
  an internal approval notification is treated as successful.
- Added an OCI single-VM production deployment layer with Caddy-managed TLS,
  loopback-only administration, private PostgreSQL networking, required trusted
  lead-ingress authentication, bounded n8n execution retention, and backup/
  restore guidance. No cloud resources are provisioned by the repository.
- Added a containerized local stack for the FlowPilot API, PostgreSQL 17, and
  n8n 2.37.10, with ordered migrations and persistent volumes.
- Added reproducible Python runtime and development dependency locks.
- Added AI lead analysis and validated plain-text response drafting.
- Added n8n lead intake, priority routing, PostgreSQL persistence, Google Sheets
  persistence, and HubSpot contact synchronization.
- Added human approval before lead email and one guarded follow-up.
- Added atomic idempotency, durable workflow stages, email uncertainty
  quarantine, and partial-work reconciliation.
- Added transactional reliability events, recovery-run history, and privacy-safe
  operational views.

This project has not adopted a release version or historical release-date
scheme yet.
