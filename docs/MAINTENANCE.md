# Dependency and container maintenance

Checked: 2026-09-17

FlowPilot keeps production container references immutable by pairing a readable
tag with a manifest digest. A digest is an identity, not a timestamp. This
review did not obtain authoritative registry publication timestamps, so image
age is recorded as **age not verified** rather than inferred from a SHA or a
locally cached image creation time.

## Production image inventory

| Use | Image and tag | Pinned manifest digest | Age verifiable | Refresh recommendation |
| --- | --- | --- | --- | --- |
| API build/runtime | `python:3.11.9-slim-bookworm` | `sha256:8fb099199b9f2d70342674bd9dbccd3ed03a258f26bbd1d556822c6dfc60c317` | No — age not verified | Routine upstream/security review; no pin change recommended from unverified age. |
| PostgreSQL and migrations | `postgres:17-alpine` | `sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73` | No — age not verified | Routine PostgreSQL 17 patch/security review; no pin change recommended from unverified age. |
| Workflow runtime | `n8nio/n8n:2.37.10` | `sha256:307d6065be25619aa24cfc63a7c2f04ca56d084a08c05c8e9f189a89f353b1ec` | No — age not verified | Hold the certified n8n version until a separately reviewed compatibility upgrade. |
| Public edge | `caddy:2.10.2-alpine` | `sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d` | No — age not verified | Routine upstream/security review; no pin change recommended from unverified age. |

`compose.postgres.yml` uses the mutable `postgres:17-alpine` tag only for the
legacy database-only local-development path. It is not part of the production
Compose image inventory. `compose.yml` pins both the production PostgreSQL
service and the one-shot migration service to the same manifest digest.

## Safe digest refresh procedure

1. Open a dedicated maintenance branch and pull request. Do not mix dependency
   or image updates with feature work.
2. Review the upstream release notes and security advisories. Record the reason
   for the proposed refresh; do not infer age from a digest.
3. Inspect the candidate tag from the registry, for example with
   `docker buildx imagetools inspect IMAGE:TAG`. Confirm the expected manifest
   digest and both `linux/amd64` and `linux/arm64` support for the OCI Ampere A1
   target before editing the repository.
4. Change the readable tag and digest together. Never replace an immutable pin
   with a mutable tag in the production Dockerfile or Compose files.
5. Build from a clean cache boundary and run `docker compose config` for the
   base stack plus the production override with synthetic secure values.
6. Run the complete default test suite, every optional PostgreSQL 17
   integration test, and the production ingress/security test set.
7. Repeat the Phase 10A runtime gate: migrations 001 through 008 on a fresh
   database and an existing volume, service health, loopback-only API/n8n,
   internal-only PostgreSQL, Caddy's exact public route policy, ingress 401/204
   behavior, Docker DNS connectivity, and PostgreSQL/n8n persistence across
   `docker compose down` and restart.
8. Review the image configuration, non-root/runtime restrictions, vulnerability
   findings, and application logs before approval. Preserve the previous
   working digest and database backup so rollback is explicit.

Do not accept a new digest solely because it is newer. Do not claim the refresh
is safe from static manifest inspection alone.

## Python dependency maintenance

Direct versions live in `requirements.txt`; complete runtime and development
graphs live in `requirements.runtime.lock` and `requirements.dev.lock`. Update
them only in a dedicated reviewed pull request using two clean Python 3.11
environments as described in [PHASES.md](PHASES.md#reproducible-development).
Run `python -m pip check`, verify the development lock remains a strict superset
of the runtime lock, and pass the same default, PostgreSQL, Docker build, and
production-security gates before acceptance.
