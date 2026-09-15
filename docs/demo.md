# FlowPilot five-minute demo

This walkthrough demonstrates the product and its reliability model. Use local
test accounts and non-sensitive sample data. External credentials are optional;
skip the corresponding live side effect when they are unavailable.

## Prepare the stack

1. Copy `.env.example` to `.env` and replace the local database password and
   n8n encryption-key placeholders.
2. Add an OpenAI key only if live analysis is part of the demo.
3. Run `docker compose up --build`.
4. Confirm <http://localhost:8000/health> returns `{"status":"healthy"}`.
5. Open <http://localhost:8000/docs> and <http://localhost:5678>.
6. Complete the local n8n owner setup, import all four files from `n8n/`, select
   the locally configured credentials, and activate the workflows needed for
   the demonstration.

## Product path

1. Submit a sample lead through the lead-intake webhook with an
   `Idempotency-Key` that contains no customer information.
2. Show the structured category, priority, score, summary, and recommended
   action returned by FlowPilot.
3. Query the `leads` row in PostgreSQL and show that the validated analysis is
   durable before downstream processing.
4. If configured, show the corresponding Google Sheets row and exact-email
   HubSpot contact create/update result.
5. Show the persisted plain-text draft in `pending_approval` and the internal
   notification. Do not send the model output directly to the lead.
6. Open the review link. Demonstrate that GET only renders the review page and
   that an explicit POST is required to approve or reject.
7. Approve once and show the guarded delivery state. A repeated decision must
   not send again.
8. Show the follow-up as scheduled for 72 hours after the confirmed initial
   send. Do not wait for it during the demo.
9. Resubmit the identical payload with the same idempotency key and show the
   completed public response replay without repeated business side effects.
10. Open the reconciliation and reliability-summary views to show how an
    operator sees work that requires investigation without seeing private
    message or draft content.

## Deterministic reliability demonstration

Use the existing mocked ambiguous-SMTP test rather than adding a production
failure switch:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_followup_workflow.py::test_ambiguous_smtp_result_marks_uncertain_without_raw_details -q
```

The test proves that an ambiguous provider result is classified as `uncertain`
and that raw provider details are not exposed. Follow with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_email_recovery_workflow.py::test_no_email_retry_or_uncertain_requeue_exists -q
```

This proves the recovery workflow contains no automatic resend or uncertain
requeue. The demonstration is deterministic, credential-free, and cannot alter
production behavior.

For a PostgreSQL-backed demonstration, use a disposable PostgreSQL 17 container
and run the stale-work integration case documented in the README. It creates
isolated test schemas and removes them after the test.

## Reset and cleanup

Run `docker compose down` to stop the stack while preserving PostgreSQL and n8n
state. Use `docker compose down --volumes` only when the local demo data is no
longer needed and a destructive reset is intentional.
