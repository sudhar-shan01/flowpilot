# FlowPilot end-to-end certification

Certification date: 2026-09-16

Baseline: `main` at `d23cd133e372d6d4da1f62145f962c9208c29337`

Certification branch: `codex/final-e2e-certification`

This gate certifies the current single-VM pilot design with synthetic lead data
and test doubles. It does not call OpenAI, Google Sheets, HubSpot, SMTP, or any
customer system. It does not claim exactly-once delivery.

## Execution map

The authenticated public lead route terminates at Caddy. Caddy checks the
configured webhook secret through the private FastAPI verifier, removes that
header, and forwards only the accepted request to n8n. The lead workflow then:

1. validates the idempotency key and exact five-field lead envelope;
2. atomically claims a keyed request;
3. obtains and validates the structured analysis;
4. advances keyed work to `business_started` before the first durable lead side
   effect;
5. inserts the lead, appends Sheets, and creates or updates HubSpot;
6. obtains and validates a plain-text draft, persists it, and issues the
   expiring approval token;
7. requires confirmed recipient acceptance for the internal notification;
8. records the exact public response as `business_complete`, then completes the
   idempotency record.

The approval GET path only validates basic link syntax and renders a no-store,
no-referrer confirmation page. Only POST can atomically consume the token and
change `pending_approval` to `approved` or `rejected`. Approval claims the exact
persisted recipient, subject, and body before SMTP. Confirmed acceptance records
`sent` and schedules one follow-up at `initial_response_sent_at + 72 hours`.

The hourly follow-up workflow atomically claims at most 20 due rows with `FOR
UPDATE SKIP LOCKED`. Its fixed plain-text email uses the persisted recipient and
configured sender. The hourly recovery workflow never sends: stale email claims
become `uncertain`, stale safe idempotency completion is finalized, and unsafe
or partial work moves to operator reconciliation. Audit triggers record state
transitions while hashing idempotency keys; the reconciliation view exposes only
sanitized references and operator-facing states.

## Certification matrix

| Scenario | Expected behavior | Observed result | Test/reference | PASS / FAIL | Notes |
| --- | --- | --- | --- | --- | --- |
| Authenticated ingress | Missing/wrong secret rejected; exact secret accepted by verifier | Missing/wrong returned 401; exact secret returned 204 | `tests/test_ingress_security.py`; disposable Compose checks | PASS | Public authenticated webhook reached n8n routing; inactive disposable workflow returned 404 after authorization. |
| Lead validation | Malformed, missing, blank, invalid, oversized, and extra fields rejected | FastAPI returned 422; Caddy returned 413 above 64 KB | `tests/test_leads.py`; `tests/test_idempotency_workflow.py` | PASS | Extra public fields are now rejected before claim or AI work. |
| Happy path | One durable lead, draft, approval, initial send, +72h follow-up, audit trail, clean queue | Full synthetic durable lifecycle completed with one initial send and one follow-up | `test_complete_approved_lifecycle_has_one_send_and_clean_queue` | PASS | Sheets, HubSpot, AI, and SMTP were deterministic doubles, not real providers. |
| Reject path | Token consumed once, draft rejected, no customer email or follow-up | Rejected row remained unsent with no follow-up and clean queue | `test_complete_reject_lifecycle_never_claims_customer_email_or_followup` | PASS | Exact persisted draft remained unchanged. |
| Sheets stage | Runs only after PostgreSQL and maps persisted fields | Graph and mappings validated; synthetic stage succeeded | `tests/test_google_sheets_persistence.py` | PASS | No live Google account used. |
| HubSpot stage | Search by email, then create/update once | Graph, mappings, sanitization, and synthetic stage validated | `tests/test_hubspot_crm.py` | PASS | No live HubSpot account used. |
| Draft generation | Structured plain text only; stored before notification | Schema, ordering, persistence, and failure routes validated | `tests/test_lead_drafts.py`; `tests/test_email_drafts_workflow.py` | PASS | No real AI call used. |
| Internal approval notification | Success only after confirmed recipient acceptance | Accepted recipient proceeded; rejection, error, contradictory, and malformed results returned sanitized `EMAIL_ERROR` | `test_internal_notification_requires_confirmed_recipient_acceptance`; `test_internal_notification_nonacceptance_is_sanitized` | PASS | This gate fixed the previous ambiguous-result false success. |
| Approval GET / scanner | Repeated GET has no mutation, token consumption, AI, or email | Read-only graph remains isolated from every side-effect node | `tests/test_human_approval_workflow.py` | PASS | Token is only a validated hidden form value and is not visibly rendered. |
| Approval POST | One valid unexpired matching token may transition once | Atomic guarded update; concurrent approve/reject produced exactly one winner | `test_concurrent_approval_double_click_allows_exactly_one_decision`; Phase 6 tests | PASS | Wrong, expired, replayed, cross-lead, and legacy/null tokens remain blocked. |
| Idempotent replay | Same key and normalized payload executes once and safely replays | One owner per key; replay rebuilt only public response fields | Phase 8A unit and PostgreSQL tests | PASS | Unkeyed requests intentionally retain backward-compatible at-least-once risk. |
| Idempotency conflict | Same key plus different payload returns conflict | Atomic claim returned conflict without mutation | `tests/integration/test_idempotency_postgres.py` | PASS | Plaintext key never enters audit events. |
| Concurrent duplicate | One owner; duplicate is in-progress or completed replay | Genuine concurrent PostgreSQL claims produced one owner and one row | Phase 8A integration tests; certification burst tests | PASS | No provider side effect was duplicated. |
| Failed/recovery owner | Failed or partial original work is not blindly replayed | Duplicates returned `REQUEST_FAILED` or reconciliation-required behavior | Phase 8A/8B2 tests | PASS | Manual operator decision is required for partial work. |
| AI failure | Sanitized error, no false success | Pre-business failure becomes failed; invalid output returns sanitized 502/503 | `tests/test_leads.py`; lead workflow tests | PASS | No provider payload is returned. |
| Persistence failure | No downstream provider call; sanitized error | PostgreSQL gate prevents Sheets/HubSpot; keyed partial state is retained safely | Phase 3A and Phase 8B2 tests | PASS | Completed external effects are not rolled back. |
| Sheets/HubSpot/draft failure | No later success response | Each failure enters the sanitized error path; keyed partial work requires reconciliation | Phase 3B–5 and Phase 8B2 tests | PASS | No automatic replay. |
| Approval persistence failure | No email before atomic authorization | Email is unreachable until successful authorization and send claim | Phase 6/8B1 tests | PASS | Browser receives sanitized response. |
| Initial SMTP explicit failure | `failed`; draft remains approved | Guarded `sending -> failed` transition verified | Phase 8B1 unit/PostgreSQL tests | PASS | No return to pending approval. |
| Initial SMTP ambiguity | `uncertain`; no automatic resend | Timeout/malformed/unknown acceptance routes to guarded uncertain state | Phase 8B1 tests | PASS | Accepted-send plus DB-write failure remains `sending`, then stale recovery quarantines it as `uncertain`. |
| Follow-up eligibility | Exactly one scheduled at confirmed send +72h | Database interval was exactly 259,200 seconds | Happy-path certification and Phase 7 integration tests | PASS | Rejected, failed, uncertain, future, sent, cancelled, and unsent rows are ineligible. |
| Follow-up concurrency | Workers cannot claim the same row | `SKIP LOCKED` and guarded claim timestamp tests passed | `tests/integration/test_followup_postgres.py` | PASS | Batch is bounded to 20. |
| Follow-up SMTP ambiguity | Explicit reject fails; accepted sends; ambiguous quarantines | All three outcomes and stale `sending` recovery verified | Phase 8B1 tests | PASS | No automatic retry/replay. |
| Recovery sweep | Stale states move only to their safe terminal/review states | Threshold boundaries, malformed payload, legacy NULL stage, and two-run idempotence passed | Phase 8B1/8B2/8C tests | PASS | Zero-change runs still record one summary per invocation. |
| Reconciliation queue | Only attention items; friendly labels and correct age | Queue contents, privacy, and latest recovery-event timestamp passed | Phase 8C unit/PostgreSQL tests | PASS | Healthy completed lifecycle left the queue empty. |
| Migrations 001-008 | Fresh, rerun, upgrade, and concurrent runners preserve data | PostgreSQL 17.11 suite passed; concurrent runners serialized via advisory lock | PostgreSQL integration suite; certification migration test | PASS | Historical migration files were not rewritten. |
| Workflow import | All four exports import into n8n 2.37.10 | `Successfully imported 4 workflows` | Isolated pinned n8n container | PASS | Credential references remain unresolved until owner binds local credentials. |
| Fresh Compose stack | Build and all five services reach expected terminal/healthy state | PostgreSQL, API, n8n, and Caddy healthy; migration job completed | Disposable project `flowpilot-cert-stack` | PASS | Production-like local HTTP was used; public DNS/TLS was not. |
| Network exposure | Caddy public; API/n8n loopback; PostgreSQL internal only | Ports and Docker networks matched policy; PostgreSQL had no host binding | Compose runtime inspection | PASS | Public `/docs`, OpenAPI, editor, and unknown routes returned 404. |
| Restart/persistence | Service restarts and down/up preserve state | Each service recovered healthy; one PostgreSQL marker and four n8n workflows remained | Disposable Compose restart checks | PASS | `down -v` was never used. |
| Security/privacy | No committed secrets, public admin surfaces, plaintext audit keys, or privileged/socket access | Repository scan found only negative-test secret names; `.env` is ignored; containers unprivileged | Security scan, Docker inspection, Phase 8C privacy tests | PASS | Caddy is the sole public edge. |
| Query efficiency | Operational queries use bounded/indexed access | Follow-up plan used `leads_followup_due_index` on 5,000 synthetic rows | `test_operational_due_followup_query_uses_partial_index` | PASS | No external call occurs while a database lock is held. |
| Burst/concurrency | Modest duplicate load produces one side-effect set per unique key | Claim bursts 1/10/25/50 passed; 25 full synthetic submissions yielded 20 unique completions and 5 duplicates with zero recovery/uncertain rows | Certification PostgreSQL burst tests | PASS | Full provider stages were counters/test doubles. |

## Failure boundaries

Before `business_started`, a keyed analysis failure is safely terminal `failed`.
After `business_started`, failures in lead persistence, Sheets, HubSpot, draft
generation/persistence, approval-token issuance, or internal notification cannot
be replayed safely and therefore become `recovery_required`. A valid
`business_complete` response can be finalized by recovery without replaying
business work; malformed completion requires review.

Approval authorization and each email claim are atomic PostgreSQL transitions.
An explicit SMTP rejection becomes `failed`; confirmed recipient acceptance can
become `sent`; any ambiguous handoff becomes `uncertain`. If SMTP acceptance is
followed by a database-write failure, the row remains `sending` and the recovery
sweep quarantines it as `uncertain` after 30 minutes. The same rule applies to
initial and follow-up email. Nothing automatically resends an uncertain email.

## Performance and efficiency observations

The keyed lead intake path performs seven short PostgreSQL round trips: claim,
business-start boundary, lead insert, draft update, token issue,
business-complete record, and final completion. It makes two FlowPilot AI API
calls, one Sheets append, two HubSpot calls (search plus create/update), and one
internal SMTP handoff. These operations are sequential because each consumes or
guards the preceding result. Approval adds three short database statements and
one SMTP handoff. Follow-up uses one bounded claim query for up to 20 rows, then
one guarded result write per claimed row. Recovery is one database statement.

No database transaction holds row locks across an external network call. Primary
keys, the approval-token partial unique index, idempotency primary key, due-work
partial indexes, recovery indexes, and audit entity/type indexes cover the
operational predicates inspected. On 5,000 synthetic leads PostgreSQL selected
`leads_followup_due_index` for the due-work plan; no missing operational index or
N+1 query requiring a code change was demonstrated.

Observed local PostgreSQL claim-only burst elapsed times were 133.88 ms (1),
236.46 ms (10), 504.67 ms (25), and 979.51 ms (50). The 25-request synthetic
full lead-path run produced 20 unique completions and five safe duplicates; its
observed request latency was p50 1,913.20 ms, p95 2,026.44 ms, and max 2,040.68
ms. These figures include Windows-to-Docker `exec` process overhead and are not
production latency promises.

One idle-stack snapshot showed PostgreSQL 23.75 MiB, API 40.21 MiB, n8n 354.7
MiB, and Caddy 12.09 MiB, with each container at 0.19% CPU or less. This is a
single workstation snapshot, not a capacity guarantee. Production n8n success
execution data remains disabled and error data is bounded by age/count pruning.

## Workflow structure review

**Keep:** explicit trust-boundary sanitizers, the keyed business-start and
business-complete safety boundaries, approval GET/POST separation, atomic email
claims, bounded `SKIP LOCKED` follow-up claiming, and the side-effect-free
recovery workflow.

**Simplify later only with evidence:** the strict public-response JSON validator
is repeated in lead completion and recovery, and email-address/SMTP-result
classification is intentionally repeated at distinct workflow boundaries. An
additive database function or shared tested sub-workflow could reduce drift, but
extracting it now would increase migration and orchestration risk without a
demonstrated runtime benefit.

**Do not change:** reliability-critical guard predicates, claim tokens/times,
failure routing, recovery thresholds, or audit-trigger behavior. The current
Code nodes are not aesthetic duplication: each removes untrusted or sensitive
fields at a specific provider/database boundary.

## Known limitations

- OpenAI, Google Sheets, HubSpot, and SMTP were synthetic doubles. No real
  external-provider credential or customer system was used.
- The four workflows were imported into n8n 2.37.10, but the disposable instance
  intentionally had no provider credentials and the workflows remained inactive.
  Consequently, authenticated Caddy-to-n8n routing was observed, but a live
  provider-backed public webhook execution was not attempted.
- Local Compose used HTTP on high ports. OCI DNS, public certificate issuance,
  automatic HTTPS renewal, cloud firewall rules, backups to off-VM storage, and
  restore on a separate VM still require owner-run deployment verification.
- The burst test exercises the real PostgreSQL workflow SQL and synthetic
  provider side-effect boundaries. It is not a production network benchmark.
- Unkeyed webhook requests remain supported for Phase 1-9 compatibility and do
  not receive duplicate protection.
- External systems do not offer a distributed exactly-once transaction with
  FlowPilot. Partial and ambiguous effects deliberately require reconciliation;
  uncertain email is never automatically replayed.
- The current deployment is one VM and has no high availability.

## Verification commands and results

- Focused reliability/security/deployment suite: **203 passed**.
- Default suite: **239 passed, 86 skipped** (optional PostgreSQL tests skipped).
- PostgreSQL 17.11 integration suite: **86 passed**.
- n8n 2.37.10 import: **four workflows imported successfully**.
- Base and production Compose rendering: **passed**.
- Fresh production-style Compose build/start, individual restarts, down/up, and
  state persistence: **passed**.
- Final whitespace check: recorded with the certification commit.
