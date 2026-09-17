# Oracle Cloud pilot deployment

This runbook prepares a professional single-VM pilot. It does not provision
resources automatically and does not create paid Oracle Cloud Infrastructure
(OCI) resources. Review the architecture, current Free Tier terms, capacity,
costs, home region, domain, and backup policy before a live deployment.

## Prerequisites

- A reviewed FlowPilot commit or tag
- An OCI tenancy and compartment with an owner-selected home region
- Permission to manage one Compute instance, its VCN/VNIC, network security
  group (NSG) or security list, public IP, and boot-volume backups
- A domain you control for trusted production webhooks, or an external IP for
  temporary infrastructure testing only
- Docker Engine and Docker Compose v2 on the VM
- External provider accounts only for integrations the pilot will use

Do not create resources from this repository blindly. The owner must choose the
OCI tenancy, compartment, home region, availability domain, shape, domain,
budget controls, and retention policy. Account creation commonly requires
identity verification and a payment card; the owner must review current Oracle
terms before proceeding.

## VM recommendation

Prefer an Always Free-eligible `VM.Standard.A1.Flex` Ampere A1 instance where
capacity exists. A practical pilot allocation is 2 OCPUs and 12 GB RAM, subject
to the tenancy's current Free Tier allowance. Select the current standard
Ubuntu 24.04 LTS `aarch64` platform image for the Arm shape; OCI documents the
standard Ubuntu image as supported on Arm. Use an owner-approved boot-volume
size and keep Docker's data directory on that durable boot or attached block
volume. Size CPU, memory, disk, and IOPS from observed pilot traffic.

This is not an HA design. Keep deletion protection and a snapshot schedule under
owner control; preserving a boot volume on instance termination must be an
explicit choice.

## Always Free and capacity caveats

Always Free eligibility is determined by the OCI Console and current Oracle
terms, not by this repository. Eligible compute and block-volume resources must
be created in the tenancy's home region. Ampere A1 capacity is not guaranteed;
an `out of host capacity` response can require another availability domain or a
later attempt. Do not select a paid shape, image, storage tier, backup schedule,
or amount of capacity without owner approval and budget alarms.

Oracle documents that idle Always Free instances may be reclaimed based on
seven-day CPU, network, and (for A1) memory utilization. Free limits, backup
allowances, and reclamation policy can change. Confirm the `Always Free-eligible`
label and current limits immediately before provisioning. An Always Free VM is
not an availability guarantee or a production SLA.

## ARM64 image compatibility

The exact repository pins were inspected on 2026-09-16 with
`docker buildx imagetools inspect`. Each top-level digest is a multi-platform
OCI index containing a Linux ARM64 manifest, so Docker can select the native
Ampere A1 image without changing a pin:

| Component | Pinned reference | ARM64 manifest |
| --- | --- | --- |
| PostgreSQL | `postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73` | `linux/arm64/v8` |
| n8n | `n8nio/n8n:2.37.10@sha256:307d6065be25619aa24cfc63a7c2f04ca56d084a08c05c8e9f189a89f353b1ec` | `linux/arm64` |
| Caddy | `caddy:2.10.2-alpine@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d` | `linux/arm64/v8` |
| FlowPilot Python base | `python:3.11.9-slim-bookworm@sha256:8fb099199b9f2d70342674bd9dbccd3ed03a258f26bbd1d556822c6dfc60c317` | `linux/arm64/v8` |

Re-run those manifest inspections during a future pin update. Do not replace a
multi-platform index digest with an architecture-specific child digest unless
the deployment is intentionally restricted to that architecture.

## OCI network and firewall paths

Create or select a VCN with an internet gateway, public subnet, and route to the
internet gateway. Attach a dedicated NSG to the VM VNIC when possible; a
subnet security list is also supported but affects every VNIC in that subnet.
Configure stateful ingress rules:

- TCP `80` and `443` from the intended webhook/approval audience
- TCP `22` only from the approved administrator CIDR; OCI Bastion is an
  alternative when the instance is designed without direct SSH exposure

Do not create NSG, security-list, host-firewall, or public rules for `5432`,
`5678`, or `8000`. Confirm the Ubuntu host firewall agrees with the OCI rules.

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

For an OCI pilot, a root-owned `.env.prod` with mode `600` is the simplest
supported runtime mechanism. OCI Vault may be used as the owner-controlled
source of truth, but Vault, dynamic groups, IAM policies, retrieval, and
rotation are deliberately not automated here. If Vault is used, grant the VM
only the minimum secret-read permission and materialize `.env.prod` without
printing values to shell history or deployment logs.

For a temporary external-IP test, set `FLOWPILOT_CADDY_SITE` and
`FLOWPILOT_PUBLIC_ORIGIN` to `http://EXTERNAL_IP` and set
`FLOWPILOT_PUBLIC_PROTOCOL=http`. Do not send real leads, approval tokens, or
credentials over HTTP. A trusted pilot requires DNS and HTTPS.

## Domain, DNS, and TLS

Create an OCI **reserved public IPv4 address** and assign it to the primary
private IP on the instance VNIC. Unlike an ephemeral public IP, a reserved IP
can be unassigned and moved to a replacement instance. Point the owner-approved
DNS `A` record at it; add `AAAA` only when IPv6 routing and security rules were
explicitly configured. Set `FLOWPILOT_CADDY_SITE` to the domain with no scheme.
Caddy obtains and renews a public certificate automatically after DNS resolves
and ports `80` and `443` reach the VM. Persistent Caddy volumes retain
certificate state.

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

The editor is not public. From an approved administrator workstation, create an
SSH tunnel to VM loopback, using the restricted public SSH path or OCI Bastion:

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

1. Scheduled OCI boot/block-volume backups with owner-approved retention,
   region-copy policy, and a check against current Free Tier allowances.
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
`N8N_ENCRYPTION_KEY`, restore the OCI boot/block-volume backup (or the n8n
volume from a consistent backup), start PostgreSQL, and restore the logical
database dump with `pg_restore`. Run the migration service afterward so a
backup from an older revision advances through all committed migrations.

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

- OCI tenancy, compartment, home region, availability domain, Ampere A1
  capacity, shape allocation, boot-volume size, and budget alarms
- VCN, subnet, NSG/security-list ownership, reserved public IP, domain, DNS,
  TLS contact, and permitted webhook sources
- Direct SSH versus OCI Bastion administration and allowed administrator CIDRs
- OCI Vault/IAM ownership and rotation schedule, if Vault is used
- Backup destination, snapshot frequency, retention, recovery objectives, and
  restore-test owner
- SMTP, HubSpot, Google Sheets, and AI provider accounts/data-processing terms
- Whether public `/health` is acceptable for the client
