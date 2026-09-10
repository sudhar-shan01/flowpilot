ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS approval_token UUID,
    ADD COLUMN IF NOT EXISTS approval_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS approval_decided_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS initial_response_sent_at TIMESTAMPTZ;

ALTER TABLE leads
    DROP CONSTRAINT IF EXISTS leads_draft_status_check;

ALTER TABLE leads
    ADD CONSTRAINT leads_draft_status_check
    CHECK (
        draft_status IS NULL
        OR draft_status IN ('pending_approval', 'approved', 'rejected')
    );

CREATE UNIQUE INDEX IF NOT EXISTS leads_approval_token_unique
    ON leads (approval_token)
    WHERE approval_token IS NOT NULL;
