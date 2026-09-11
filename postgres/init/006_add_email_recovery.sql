BEGIN;

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS initial_response_delivery_status VARCHAR(9),
    ADD COLUMN IF NOT EXISTS initial_response_claim_token UUID,
    ADD COLUMN IF NOT EXISTS initial_response_claimed_at TIMESTAMPTZ;

ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_initial_response_delivery_status_check;
ALTER TABLE leads ADD CONSTRAINT leads_initial_response_delivery_status_check
    CHECK (
        initial_response_delivery_status IS NULL
        OR initial_response_delivery_status IN ('sending', 'sent', 'failed', 'uncertain')
    );

ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_followup_status_check;
ALTER TABLE leads ADD CONSTRAINT leads_followup_status_check
    CHECK (
        followup_status IS NULL
        OR followup_status IN ('scheduled', 'sending', 'sent', 'failed', 'cancelled', 'uncertain')
    );

CREATE INDEX IF NOT EXISTS leads_initial_response_sending_index
    ON leads (initial_response_claimed_at, id)
    WHERE initial_response_delivery_status = 'sending';

CREATE INDEX IF NOT EXISTS leads_followup_sending_index
    ON leads (followup_claimed_at, id)
    WHERE followup_status = 'sending';

COMMIT;
