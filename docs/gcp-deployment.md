# Google Cloud pilot deployment

This runbook prepares a professional single-VM pilot. It does not provision
resources automatically and does not create chargeable Google Cloud resources.
Review the architecture, costs, region, domain, and backup policy before a live
deployment.

## Prerequisites

- A reviewed FlowPilot commit or tag
- A Google Cloud project with billing controlled by the owner
- Permission to manage one Compute Engine VM, firewall rules, and optionally
  Secret Manager and persistent-disk snapshots
- A domain you control for trusted production webhooks, or an external IP for
  temporary infrastructure testing only
- Docker Engine and Docker Compose v2 on the VM
- External provider accounts only for integrations the pilot will use

Do not create resources from this repository blindly. The owner must choose the
GCP project, region, machine class, domain, budget, and retention policy.

## VM recommendation

Start with an Ubuntu 24.04 LTS Compute Engine VM with 2 vCPUs, 8 GB RAM, and at
least a 40 GB balanced persistent boot disk. Place Docker's data directory on
durable persistent-disk storage. Enable automatic restart and OS Login where
appropriate. Size CPU, memory, disk, and IOPS from observed pilot traffic.

This is not an HA design. Keep deletion protection and a snapshot schedule under
owner control; preserving a disk on VM deletion must be an explicit choice.

## Firewall and network paths

Allow inbound:

- TCP `80` and `443` from the intended webhook/approval audience
- TCP `22` only from an approved administrator CIDR, or use IAP TCP forwarding
  from Google's documented `35.235.240.0/20` range

Do not create public rules for `5432`, `5678`, or `8000`.

```text
Internet --80/443--> Caddy
                       |-- /health ----------------------> FlowPilot API
                       |-- authenticated lead webhook ---> n8n
                       `-- approval GET/POST ------------> n8n

SSH tunnel --127.0.0.1:5678--> n8n editor
SSH tunnel --127.0.0.1:8000--> API docs

n8n/API --backend network--> PostgreSQL:5432
n8n ----edge network-------> API:8000 and external providers
Caddy --edge network-------> n8n/API
```

The `backend` Docker network is internal. Caddy is not attached to it and cannot
connect directly to PostgreSQL.

## Install Docker and check out FlowPilot

Use Docker's current official Ubuntu installation instructions rather than a
copied curl-to-shell installer. Then verify the installed tools:

```bash
docker version
docker compose version
git --version
```

Require Compose v2.24 or newer. Check out the exact reviewed revision, not an
unreviewed moving branch:

```bash
sudo install -d -o "$USER" -g "$USER" /opt/flowpilot
git clone https://github.com/sudhar-shan01/flowpilot.git /opt/flowpilot
cd /opt/flowpilot
git fetch --tags origin
git checkout --detach REVIEWED_COMMIT_OR_TAG
git status
```

## Production environment and secrets

Copy the template outside Git's tracked state and restrict it:

```bash
cd /opt/flowpilot
cp .env.prod.example .env.prod
chmod 600 .env.prod
```

Set every required value without printing it to deployment logs:

- `POSTGRES_PASSWORD`: strong database password
- `N8N_ENCRYPTION_KEY`: long random key; escrow it independently
- `FLOWPILOT_WEBHOOK_INGRESS_SECRET`: the application enforces at least 32
  characters. Use an ASCII hex or base64url value generated from at least 32
  random bytes and share it only with trusted lead sources.
- `FLOWPILOT_CADDY_SITE`: domain for automatic HTTPS, for example
  `flowpilot.example.com`
- `FLOWPILOT_PUBLIC_HOST`: host only, for example `flowpilot.example.com`
- `FLOWPILOT_PUBLIC_ORIGIN`: origin with scheme, for example
  `https://flowpilot.example.com`
- `OPENAI_API_KEY`: required for live AI analysis/drafts, not for `/health`
- Internal and lead sender addresses used by the existing workflows

SMTP, HubSpot, and Google Sheets credentials stay in n8n's encrypted credential
store. Do not duplicate them into `.env.prod`.

For a GCE pilot, a root-owned `.env.prod` with mode `600` is the simplest
supported runtime mechanism. Google Secret Manager is the recommended source of
truth when available: grant only the VM service account Secret Accessor on the
specific secret and materialize an environment file without echoing it, for
example during an approved maintenance action:

```bash
umask 077
gcloud secrets versions access latest --secret=flowpilot-prod-env > /opt/flowpilot/.env.prod
```

Creating that secret, assigning IAM, and selecting rotation policy are owner
actions and are intentionally not automated here.

For a temporary external-IP test, set `FLOWPILOT_CADDY_SITE` and
`FLOWPILOT_PUBLIC_ORIGIN` to `http://EXTERNAL_IP` and set
`FLOWPILOT_PUBLIC_PROTOCOL=http`. Do not send real leads, approval tokens, or
credentials over HTTP. A trusted pilot requires DNS and HTTPS.

## Domain, DNS, and TLS

Reserve a stable external IP, then create the owner-approved DNS `A`/`AAAA`
record pointing at it. Set `FLOWPILOT_CADDY_SITE` to the domain with no scheme.
Caddy obtains and renews a public certificate automatically after ports `80`
and `443` reach the VM. Persistent Caddy volumes retain certificate state.

Do not hard-code a personal domain in the repository. If certificate issuance
fails, inspect Caddy logs and DNS/firewall state; do not bypass browser TLS
warnings for production.

## Start and verify

Define the production command once in the operator shell if desired, or run it
explicitly as shown. Configuration validation requires no containers:

```bash
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml config --quiet
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml up --build -d
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml ps
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml logs migrations
```

The expected startup chain is PostgreSQL health, migrations `001` through `008`,
API health, n8n health, then Caddy. A migration failure is visible in restricted
logs and blocks the dependent services.

Verify public ingress without exposing secrets:

```bash
curl --fail --show-error "${FLOWPILOT_PUBLIC_ORIGIN}/health"
curl --fail --show-error -o /dev/null "${FLOWPILOT_PUBLIC_ORIGIN}/webhook/flowpilot/lead" || true
```

The second request is deliberately unauthenticated and should fail; do not put
the real shared secret in shell history or diagnostic output. Configure the
trusted source to send it as `X-FlowPilot-Webhook-Secret` over HTTPS.

## n8n first-time setup

The editor is not public. From an administrator workstation, create an SSH
tunnel (or equivalent IAP tunnel) to VM loopback:

```bash
ssh -L 5678:127.0.0.1:5678 -L 8000:127.0.0.1:8000 ADMIN@VM_EXTERNAL_IP
```

Open <http://localhost:5678> to create the n8n owner and
<http://localhost:8000/docs> to inspect the API. On a brand-new n8n volume,
import the four mounted workflow exports once:

```bash
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml exec n8n n8n import:workflow --separate --input=/opt/flowpilot-workflows
```

Do not repeat bulk import on an existing instance. In the editor, reselect local
credentials and activate the lead, approval, follow-up, and recovery workflows.
Use PostgreSQL host `postgres`, port `5432`, and the configured database/user.
The `FLOWPILOT_API_URL` is already `http://flowpilot-api:8000`.

Create the existing SMTP, HubSpot, and Google Sheets credentials in n8n. Never
export credential payloads. Successful execution data is disabled in production;
error execution data is pruned after seven days by default and the editor must
remain restricted because failures can contain customer or token metadata.

## Operations

Use restricted operator access for routine checks:

```bash
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml ps
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml logs --since=30m flowpilot-api n8n postgres migrations caddy
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml restart flowpilot-api
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml down
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml up -d
```

`down` preserves named volumes. **DESTRUCTIVE:** never run
`docker compose down -v` unless the owner has approved permanent deletion and a
verified restore exists. After restart, confirm `/health`, n8n workflow
activation, the hourly recovery workflow, and the read-only reconciliation
views described in `docs/reliability-runbook.md`.

Caddy access logging is intentionally not enabled because approval tokens are
query parameters. Container operational logs remain available, but operators
must not paste headers, URLs containing approval tokens, customer messages,
drafts, idempotency keys, or provider payloads into tickets or chat.

## Backup

Use two layers:

1. Scheduled GCE persistent-disk snapshots with an owner-approved retention and
   cross-region policy.
2. A regular PostgreSQL custom-format logical dump copied to an encrypted,
   access-controlled backup location outside the VM.

Example logical dump from a protected operator shell:

```bash
umask 077
mkdir -p /srv/flowpilot/backups
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml exec -T postgres sh -ec 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "/srv/flowpilot/backups/flowpilot-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

The n8n named volume contains its SQLite state and encrypted credential records.
Snapshot it consistently by stopping n8n during a maintenance window or by
stopping the stack before a disk snapshot. Back up `N8N_ENCRYPTION_KEY`
separately; an n8n data backup without that exact key cannot decrypt credentials.
Never place the key in the same archive as the only copy of the n8n data.

Test restores on an isolated VM or Compose project. A backup that has not been
restored is not considered verified.

## Restore

Restore into an isolated replacement VM first. Supply the original
`N8N_ENCRYPTION_KEY`, restore the persistent-disk snapshot (or the n8n volume
from a consistent backup), start PostgreSQL, and restore the logical database
dump with `pg_restore`. Run the migration service afterward so a backup from an
older revision advances through all committed migrations.

Do not restore over a live production volume. Database restore and volume
replacement are destructive recovery operations requiring owner approval.
Afterward verify counts, constraints, audit/reconciliation views, workflow
credentials, activation state, health, and one synthetic end-to-end lead.

## Update

Use a reviewed commit and a maintenance window:

```bash
cd /opt/flowpilot
git fetch origin --tags
git checkout --detach NEW_REVIEWED_COMMIT
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml config --quiet
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml build --pull
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml up -d
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml ps
docker compose --project-name flowpilot-prod --env-file .env.prod -f compose.yml -f compose.prod.yml logs migrations
```

Take a verified backup before a schema-changing update. Do not rotate
`N8N_ENCRYPTION_KEY` as part of an ordinary deployment.

## Rollback

Application/image rollback means checking out the previous reviewed commit and
running the same Compose config/build/up gates. Database migrations are
forward-only: do not assume an older application is compatible with a newer
schema. If compatibility is uncertain, stop, preserve evidence, and restore the
pre-deployment backup into an isolated replacement environment rather than
editing migration history or deleting data.

## Single-VM limitations

The VM, Docker daemon, boot disk, reverse proxy, PostgreSQL process, and n8n
process are each a single point of failure. There is no failover, autoscaling,
multi-region database, distributed rate limiter, WAF, or automated SMTP replay.
Maintenance causes downtime. The shared webhook secret authenticates a trusted
source but does not provide per-source identity or non-repudiation.

Those limits are acceptable only for an explicitly monitored pilot with tested
backups, bounded traffic, restricted administration, and an agreed recovery
objective.

## Pilot client boundary

After Phase 10A readiness review, a pilot client can use:

```text
Lead source → authenticated FlowPilot webhook → AI analysis → PostgreSQL
            → Google Sheets / HubSpot → persisted AI response draft
            → explicit human approval → email → one scheduled follow-up
            → recovery sweep / audit / reconciliation queue
```

The existing safety semantics remain: no exactly-once claim, no blind replay of
uncertain work, and no automatic resend. Phase 10 does not build a customer
dashboard, React frontend, mobile application, HA platform, or cloud resources.

## Owner decisions required before provisioning

- GCP project, billing budget/alerts, region/zone, VM class, and disk size
- Domain, static IP, DNS, TLS contact, and permitted webhook sources
- SSH versus IAP administration and allowed administrator CIDRs
- Secret Manager/IAM ownership and rotation schedule
- Backup destination, snapshot frequency, retention, recovery objectives, and
  restore-test owner
- SMTP, HubSpot, Google Sheets, and AI provider accounts/data-processing terms
- Whether public `/health` is acceptable for the client
- Repository license; it remains unresolved
