# FlowPilot reliability runbook

Phase 8C provides a read-only investigation contract. Start with
`flowpilot_reconciliation_queue`, correlate the item reference with the
append-oriented reliability events, and review the latest counter-only recovery
runs. Access to n8n execution history and downstream systems should remain
restricted to authorized operators.

## Needs review

The keyed request crossed, or may have crossed, the durable business boundary
and FlowPilot cannot prove that replay is safe. Review its hashed request
reference and ordered audit events. Then correlate authorized records in
PostgreSQL, Google Sheets, HubSpot, and n8n execution history. Do not reset the
row to `processing`, assign a new claim, or replay the workflow automatically.

## Initial email delivery uncertain

The SMTP attempt started, but acceptance was not proven. Check the mail
provider's restricted delivery history using the lead ID and attempt time. Do
not resend or clear the uncertain state solely because the message is absent
from n8n output; the provider may have accepted it before the connection failed.

## Follow-up delivery uncertain

The follow-up SMTP attempt started, but acceptance was not proven. Investigate
the provider and the lead's reliability events using the database lead ID. Do
not schedule another follow-up, reset it to `scheduled`, or send it manually
without a separate approved operational procedure.

## Recovery sweep review

Each hourly execution inserts one row into `flowpilot_recovery_runs`, including
zero-change runs. A missing recent run suggests the recovery workflow or its
database credential needs attention. Nonzero counters show what the sweep
changed; they do not authorize replay. Query the reconciliation queue next for
items requiring human investigation.

All investigation queries must avoid customer content, approval tokens, draft
text, plaintext idempotency keys, credentials, and raw provider errors. Phase
8C intentionally provides no mutation endpoint, retry engine, or automatic
reconciliation action.
