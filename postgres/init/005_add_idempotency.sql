BEGIN;

CREATE TABLE IF NOT EXISTS flowpilot_idempotency (
    idempotency_key VARCHAR(128) PRIMARY KEY,
    request_fingerprint CHAR(64) NOT NULL,
    status VARCHAR(10) NOT NULL
        CHECK (status IN ('processing', 'completed', 'failed')),
    claim_token UUID NOT NULL DEFAULT gen_random_uuid(),
    response_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    CONSTRAINT flowpilot_idempotency_completed_state_check CHECK (
        (status = 'completed' AND response_payload IS NOT NULL AND completed_at IS NOT NULL)
        OR
        (status IN ('processing', 'failed') AND response_payload IS NULL AND completed_at IS NULL)
    )
);

COMMIT;
