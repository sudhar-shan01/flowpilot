BEGIN;

CREATE TABLE IF NOT EXISTS flowpilot_reliability_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    entity_type VARCHAR(16) NOT NULL
        CHECK (entity_type IN ('lead', 'idempotency')),
    entity_ref VARCHAR(80) NOT NULL
        CHECK (BTRIM(entity_ref) <> ''),
    event_type VARCHAR(48) NOT NULL
        CHECK (event_type IN (
            'lead_created',
            'draft_pending_approval',
            'draft_approved',
            'draft_rejected',
            'idempotency_claimed',
            'idempotency_business_started',
            'idempotency_business_complete',
            'idempotency_completed',
            'idempotency_failed',
            'idempotency_recovery_required',
            'initial_email_sending',
            'initial_email_sent',
            'initial_email_failed',
            'initial_email_uncertain',
            'followup_scheduled',
            'followup_sending',
            'followup_sent',
            'followup_failed',
            'followup_uncertain',
            'followup_cancelled'
        )),
    previous_status VARCHAR(32),
    new_status VARCHAR(32),
    previous_stage VARCHAR(32),
    new_stage VARCHAR(32)
);

CREATE INDEX IF NOT EXISTS flowpilot_reliability_events_entity_index
    ON flowpilot_reliability_events (entity_type, entity_ref, occurred_at DESC, event_id DESC);

CREATE INDEX IF NOT EXISTS flowpilot_reliability_events_type_index
    ON flowpilot_reliability_events (event_type, occurred_at DESC);

CREATE TABLE IF NOT EXISTS flowpilot_recovery_runs (
    run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ran_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    initial_email_quarantined INTEGER NOT NULL DEFAULT 0
        CHECK (initial_email_quarantined >= 0),
    followups_quarantined INTEGER NOT NULL DEFAULT 0
        CHECK (followups_quarantined >= 0),
    stale_claimed_failed INTEGER NOT NULL DEFAULT 0
        CHECK (stale_claimed_failed >= 0),
    partial_work_recovery_required INTEGER NOT NULL DEFAULT 0
        CHECK (partial_work_recovery_required >= 0),
    safely_completed INTEGER NOT NULL DEFAULT 0
        CHECK (safely_completed >= 0),
    malformed_complete_recovery_required INTEGER NOT NULL DEFAULT 0
        CHECK (malformed_complete_recovery_required >= 0),
    legacy_recovery_required INTEGER NOT NULL DEFAULT 0
        CHECK (legacy_recovery_required >= 0)
);

CREATE INDEX IF NOT EXISTS flowpilot_recovery_runs_ran_at_index
    ON flowpilot_recovery_runs (ran_at DESC, run_id DESC);

CREATE OR REPLACE FUNCTION flowpilot_audit_lead_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    lead_ref TEXT := NEW.id::TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO flowpilot_reliability_events
            (entity_type, entity_ref, event_type)
        VALUES
            ('lead', lead_ref, 'lead_created');
        RETURN NEW;
    END IF;

    IF OLD.draft_status IS DISTINCT FROM NEW.draft_status THEN
        IF NEW.draft_status = 'pending_approval' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'draft_pending_approval', OLD.draft_status, NEW.draft_status);
        ELSIF NEW.draft_status = 'approved' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'draft_approved', OLD.draft_status, NEW.draft_status);
        ELSIF NEW.draft_status = 'rejected' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'draft_rejected', OLD.draft_status, NEW.draft_status);
        END IF;
    END IF;

    IF OLD.initial_response_delivery_status IS DISTINCT FROM NEW.initial_response_delivery_status THEN
        IF NEW.initial_response_delivery_status = 'sending' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'initial_email_sending', OLD.initial_response_delivery_status, NEW.initial_response_delivery_status);
        ELSIF NEW.initial_response_delivery_status = 'sent' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'initial_email_sent', OLD.initial_response_delivery_status, NEW.initial_response_delivery_status);
        ELSIF NEW.initial_response_delivery_status = 'failed' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'initial_email_failed', OLD.initial_response_delivery_status, NEW.initial_response_delivery_status);
        ELSIF NEW.initial_response_delivery_status = 'uncertain' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'initial_email_uncertain', OLD.initial_response_delivery_status, NEW.initial_response_delivery_status);
        END IF;
    END IF;

    IF OLD.followup_status IS DISTINCT FROM NEW.followup_status THEN
        IF NEW.followup_status = 'scheduled' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'followup_scheduled', OLD.followup_status, NEW.followup_status);
        ELSIF NEW.followup_status = 'sending' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'followup_sending', OLD.followup_status, NEW.followup_status);
        ELSIF NEW.followup_status = 'sent' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'followup_sent', OLD.followup_status, NEW.followup_status);
        ELSIF NEW.followup_status = 'failed' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'followup_failed', OLD.followup_status, NEW.followup_status);
        ELSIF NEW.followup_status = 'uncertain' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'followup_uncertain', OLD.followup_status, NEW.followup_status);
        ELSIF NEW.followup_status = 'cancelled' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status)
            VALUES
                ('lead', lead_ref, 'followup_cancelled', OLD.followup_status, NEW.followup_status);
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS flowpilot_audit_lead_change_trigger ON leads;
CREATE TRIGGER flowpilot_audit_lead_change_trigger
AFTER INSERT OR UPDATE OF
    draft_status,
    initial_response_delivery_status,
    followup_status
ON leads
FOR EACH ROW
EXECUTE FUNCTION flowpilot_audit_lead_change();

CREATE OR REPLACE FUNCTION flowpilot_audit_idempotency_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    request_ref TEXT := encode(sha256(convert_to(NEW.idempotency_key, 'UTF8')), 'hex');
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status = 'processing' AND NEW.workflow_stage = 'claimed' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, new_status, new_stage)
            VALUES
                ('idempotency', request_ref, 'idempotency_claimed', NEW.status, NEW.workflow_stage);
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.workflow_stage IS DISTINCT FROM NEW.workflow_stage THEN
        IF NEW.workflow_stage = 'business_started' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status, previous_stage, new_stage)
            VALUES
                ('idempotency', request_ref, 'idempotency_business_started', OLD.status, NEW.status, OLD.workflow_stage, NEW.workflow_stage);
        ELSIF NEW.workflow_stage = 'business_complete' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status, previous_stage, new_stage)
            VALUES
                ('idempotency', request_ref, 'idempotency_business_complete', OLD.status, NEW.status, OLD.workflow_stage, NEW.workflow_stage);
        END IF;
    END IF;

    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF NEW.status = 'completed' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status, previous_stage, new_stage)
            VALUES
                ('idempotency', request_ref, 'idempotency_completed', OLD.status, NEW.status, OLD.workflow_stage, NEW.workflow_stage);
        ELSIF NEW.status = 'failed' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status, previous_stage, new_stage)
            VALUES
                ('idempotency', request_ref, 'idempotency_failed', OLD.status, NEW.status, OLD.workflow_stage, NEW.workflow_stage);
        ELSIF NEW.status = 'recovery_required' THEN
            INSERT INTO flowpilot_reliability_events
                (entity_type, entity_ref, event_type, previous_status, new_status, previous_stage, new_stage)
            VALUES
                ('idempotency', request_ref, 'idempotency_recovery_required', OLD.status, NEW.status, OLD.workflow_stage, NEW.workflow_stage);
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS flowpilot_audit_idempotency_change_trigger ON flowpilot_idempotency;
CREATE TRIGGER flowpilot_audit_idempotency_change_trigger
AFTER INSERT OR UPDATE OF status, workflow_stage
ON flowpilot_idempotency
FOR EACH ROW
EXECUTE FUNCTION flowpilot_audit_idempotency_change();

CREATE INDEX IF NOT EXISTS flowpilot_idempotency_recovery_required_index
    ON flowpilot_idempotency (updated_at, idempotency_key)
    WHERE status = 'recovery_required';

CREATE INDEX IF NOT EXISTS leads_initial_response_uncertain_index
    ON leads (initial_response_claimed_at, id)
    WHERE initial_response_delivery_status = 'uncertain';

CREATE INDEX IF NOT EXISTS leads_followup_uncertain_index
    ON leads (followup_claimed_at, id)
    WHERE followup_status = 'uncertain';

CREATE OR REPLACE VIEW flowpilot_reconciliation_queue AS
WITH queue_items AS (
    SELECT
        'idempotency_request'::TEXT AS item_type,
        encode(sha256(convert_to(idempotency_key, 'UTF8')), 'hex')::TEXT AS item_ref,
        'Needs review'::TEXT AS display_status,
        status::TEXT AS technical_status,
        workflow_stage::TEXT AS technical_stage,
        TRUE AS requires_action,
        COALESCE(
            (
                SELECT MAX(event.occurred_at)
                FROM flowpilot_reliability_events AS event
                WHERE event.entity_type = 'idempotency'
                  AND event.entity_ref = encode(
                      sha256(convert_to(idempotency_key, 'UTF8')),
                      'hex'
                  )
                  AND event.event_type = 'idempotency_recovery_required'
            ),
            updated_at,
            stage_updated_at,
            created_at
        ) AS state_since
    FROM flowpilot_idempotency
    WHERE status = 'recovery_required'

    UNION ALL

    SELECT
        'initial_email'::TEXT,
        lead.id::TEXT,
        'Initial email delivery uncertain'::TEXT,
        lead.initial_response_delivery_status::TEXT,
        NULL::TEXT,
        TRUE,
        COALESCE(
            (
                SELECT MAX(event.occurred_at)
                FROM flowpilot_reliability_events AS event
                WHERE event.entity_type = 'lead'
                  AND event.entity_ref = lead.id::TEXT
                  AND event.event_type = 'initial_email_uncertain'
            ),
            lead.initial_response_claimed_at,
            lead.created_at
        )
    FROM leads AS lead
    WHERE lead.initial_response_delivery_status = 'uncertain'

    UNION ALL

    SELECT
        'followup_email'::TEXT,
        lead.id::TEXT,
        'Follow-up delivery uncertain'::TEXT,
        lead.followup_status::TEXT,
        NULL::TEXT,
        TRUE,
        COALESCE(
            (
                SELECT MAX(event.occurred_at)
                FROM flowpilot_reliability_events AS event
                WHERE event.entity_type = 'lead'
                  AND event.entity_ref = lead.id::TEXT
                  AND event.event_type = 'followup_uncertain'
            ),
            lead.followup_claimed_at,
            lead.created_at
        )
    FROM leads AS lead
    WHERE lead.followup_status = 'uncertain'
)
SELECT
    item_type,
    item_ref,
    display_status,
    technical_status,
    technical_stage,
    requires_action,
    state_since,
    GREATEST(
        0,
        FLOOR(EXTRACT(EPOCH FROM (clock_timestamp() - state_since)))::BIGINT
    ) AS age_seconds
FROM queue_items;

CREATE OR REPLACE VIEW flowpilot_reliability_summary AS
SELECT
    (
        SELECT COUNT(*)::BIGINT
        FROM flowpilot_reconciliation_queue
    ) AS items_needing_reconciliation,
    (
        SELECT COUNT(*)::BIGINT
        FROM leads
        WHERE initial_response_delivery_status = 'uncertain'
    ) AS uncertain_initial_emails,
    (
        SELECT COUNT(*)::BIGINT
        FROM leads
        WHERE followup_status = 'uncertain'
    ) AS uncertain_followups,
    (
        SELECT COUNT(*)::BIGINT
        FROM flowpilot_idempotency
        WHERE status = 'processing'
    ) AS processing_idempotency_records,
    (
        SELECT MAX(ran_at)
        FROM flowpilot_recovery_runs
    ) AS latest_recovery_run_at;

COMMIT;
