ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS draft_subject TEXT,
    ADD COLUMN IF NOT EXISTS draft_body TEXT,
    ADD COLUMN IF NOT EXISTS draft_status TEXT;

ALTER TABLE leads
    DROP CONSTRAINT IF EXISTS leads_draft_status_check;

ALTER TABLE leads
    ADD CONSTRAINT leads_draft_status_check
    CHECK (draft_status IS NULL OR draft_status = 'pending_approval');
