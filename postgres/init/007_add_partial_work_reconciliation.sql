BEGIN;

ALTER TABLE flowpilot_idempotency
    ADD COLUMN IF NOT EXISTS workflow_stage VARCHAR(17),
    ADD COLUMN IF NOT EXISTS stage_updated_at TIMESTAMPTZ;

ALTER TABLE flowpilot_idempotency
    ALTER COLUMN status TYPE VARCHAR(17);

ALTER TABLE flowpilot_idempotency
    DROP CONSTRAINT IF EXISTS flowpilot_idempotency_status_check;
ALTER TABLE flowpilot_idempotency
    ADD CONSTRAINT flowpilot_idempotency_status_check
    CHECK (status IN ('processing', 'completed', 'failed', 'recovery_required'));

ALTER TABLE flowpilot_idempotency
    DROP CONSTRAINT IF EXISTS flowpilot_idempotency_workflow_stage_check;
ALTER TABLE flowpilot_idempotency
    ADD CONSTRAINT flowpilot_idempotency_workflow_stage_check
    CHECK (
        workflow_stage IS NULL
        OR workflow_stage IN ('claimed', 'business_started', 'business_complete')
    );

ALTER TABLE flowpilot_idempotency
    DROP CONSTRAINT IF EXISTS flowpilot_idempotency_stage_timestamp_check;
ALTER TABLE flowpilot_idempotency
    ADD CONSTRAINT flowpilot_idempotency_stage_timestamp_check
    CHECK (workflow_stage IS NULL OR stage_updated_at IS NOT NULL);

ALTER TABLE flowpilot_idempotency
    DROP CONSTRAINT IF EXISTS flowpilot_idempotency_completed_state_check;
ALTER TABLE flowpilot_idempotency
    ADD CONSTRAINT flowpilot_idempotency_completed_state_check
    CHECK (
        (
            status = 'processing'
            AND completed_at IS NULL
            AND (
                (workflow_stage IS NULL AND response_payload IS NULL)
                OR (workflow_stage IN ('claimed', 'business_started') AND response_payload IS NULL)
                OR (workflow_stage = 'business_complete' AND response_payload IS NOT NULL)
            )
        )
        OR (
            status = 'completed'
            AND response_payload IS NOT NULL
            AND completed_at IS NOT NULL
            AND (workflow_stage IS NULL OR workflow_stage = 'business_complete')
        )
        OR (
            status = 'failed'
            AND response_payload IS NULL
            AND completed_at IS NULL
            AND (workflow_stage IS NULL OR workflow_stage = 'claimed')
        )
        OR (
            status = 'recovery_required'
            AND completed_at IS NULL
            AND (
                workflow_stage IS NULL
                OR workflow_stage IN ('business_started', 'business_complete')
            )
        )
    );

CREATE INDEX IF NOT EXISTS flowpilot_idempotency_processing_stage_index
    ON flowpilot_idempotency (
        (COALESCE(stage_updated_at, updated_at)),
        idempotency_key
    )
    WHERE status = 'processing';

COMMIT;
