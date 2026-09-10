BEGIN;

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS followup_status VARCHAR(9),
    ADD COLUMN IF NOT EXISTS followup_due_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS followup_claimed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS followup_sent_at TIMESTAMPTZ;

ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_followup_status_check;
ALTER TABLE leads ADD CONSTRAINT leads_followup_status_check
    CHECK (
        followup_status IS NULL
        OR followup_status IN ('scheduled', 'sending', 'sent', 'failed', 'cancelled')
    );

CREATE INDEX IF NOT EXISTS leads_followup_due_index
    ON leads (followup_due_at, id)
    WHERE followup_status = 'scheduled';

COMMIT;
