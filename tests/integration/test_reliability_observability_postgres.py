"""PostgreSQL 17 integration coverage for Phase 8C observability.

Set FLOWPILOT_TEST_PG_CONTAINER to one fresh disposable PostgreSQL 17 container.
"""

import json
import os
from pathlib import Path
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest


ROOT = Path(__file__).parents[2]
CONTAINER = os.getenv("FLOWPILOT_TEST_PG_CONTAINER")
pytestmark = pytest.mark.skipif(
    not CONTAINER, reason="Optional disposable PostgreSQL verification"
)
RECOVERY = json.loads(
    (ROOT / "n8n/flowpilot-recovery-workflow.json").read_text(encoding="utf-8")
)
RECOVERY_SQL = next(
    node["parameters"]["query"]
    for node in RECOVERY["nodes"]
    if node["name"] == "Reconcile Stale Work"
).rstrip(";")


def command() -> list[str]:
    return [
        "docker",
        "exec",
        "-i",
        str(CONTAINER),
        "psql",
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
    ]


def sql(schema: str, query: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command(),
        input=f'SET search_path TO "{schema}";\n' + query,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def apply_migrations(schema: str, through: int = 8) -> None:
    for migration in sorted((ROOT / "postgres/init").glob("*.sql")):
        if int(migration.name.split("_", 1)[0]) <= through:
            sql(schema, migration.read_text(encoding="utf-8"))


@pytest.fixture
def db():
    schema = "fp8c_" + uuid.uuid4().hex
    sql(schema, f'CREATE SCHEMA "{schema}";')
    apply_migrations(schema)
    yield schema
    sql(schema, f'DROP SCHEMA "{schema}" CASCADE;')


def seed_lead(schema: str, suffix: str = "") -> int:
    output = sql(
        schema,
        "INSERT INTO leads "
        "(name,email,company,message,budget,category,priority,lead_score,"
        "short_summary,recommended_action) VALUES ("
        f"'Sensitive Name','private{suffix}@example.com','Secret Company',"
        "'Private customer message',5000,'automation','high',88,"
        "'Private summary','Private action') RETURNING id;",
    ).stdout.strip()
    return int(output)


def insert_idempotency(
    schema: str,
    key: str,
    *,
    status: str = "processing",
    stage: str | None = "claimed",
    response: str | None = None,
    age: str = "0 minutes",
) -> None:
    stage_sql = "NULL" if stage is None else literal(stage)
    response_sql = "NULL" if response is None else literal(response) + "::jsonb"
    completed_sql = "NOW()" if status == "completed" else "NULL"
    sql(
        schema,
        "INSERT INTO flowpilot_idempotency "
        "(idempotency_key,request_fingerprint,status,response_payload,created_at,"
        "updated_at,completed_at,workflow_stage,stage_updated_at) VALUES ("
        f"{literal(key)},'{('a' * 64)}',{literal(status)},{response_sql},"
        f"NOW()-INTERVAL {literal(age)},NOW()-INTERVAL {literal(age)},"
        f"{completed_sql},{stage_sql},"
        f"CASE WHEN {stage_sql} IS NULL THEN NULL ELSE NOW()-INTERVAL {literal(age)} END);",
    )


def valid_public_response() -> str:
    return json.dumps(
        {
            "success": True,
            "route": "high",
            "message": "High-priority lead received.",
            "analysis": {
                "category": "workflow automation",
                "priority": "high",
                "lead_score": 88,
                "short_summary": "Summary",
                "recommended_action": "Call",
            },
        }
    )


def recover(schema: str) -> list[int]:
    output = sql(schema, RECOVERY_SQL).stdout.strip()
    return [int(value) for value in output.split("|")]


def test_migrations_001_through_008_apply_to_fresh_database(db):
    objects = sql(
        db,
        "SELECT relname FROM pg_class WHERE relnamespace=current_schema()::regnamespace "
        "AND relname IN ('flowpilot_reliability_events','flowpilot_recovery_runs',"
        "'flowpilot_reconciliation_queue','flowpilot_reliability_summary') "
        "ORDER BY relname;",
    ).stdout.strip().splitlines()
    assert objects == [
        "flowpilot_reconciliation_queue",
        "flowpilot_recovery_runs",
        "flowpilot_reliability_events",
        "flowpilot_reliability_summary",
    ]


def test_migration_008_reruns_and_preserves_upgrade_data():
    schema = "fp8c_upgrade_" + uuid.uuid4().hex
    sql(schema, f'CREATE SCHEMA "{schema}";')
    try:
        apply_migrations(schema, through=7)
        seed_lead(schema)
        insert_idempotency(
            schema,
            "historical-completed",
            status="completed",
            stage=None,
            response='{"success":true}',
        )
        insert_idempotency(schema, "historical-failed", status="failed", stage=None)
        insert_idempotency(
            schema,
            "historical-recovery",
            status="recovery_required",
            stage="business_started",
        )
        before = sql(
            schema,
            "SELECT (SELECT COUNT(*) FROM leads),"
            "(SELECT COUNT(*) FROM flowpilot_idempotency),"
            "(SELECT string_agg(status,',' ORDER BY status) FROM flowpilot_idempotency);",
        ).stdout
        migration = (ROOT / "postgres/init/008_add_reliability_observability.sql").read_text(
            encoding="utf-8"
        )
        sql(schema, migration)
        sql(schema, migration)
        after = sql(
            schema,
            "SELECT (SELECT COUNT(*) FROM leads),"
            "(SELECT COUNT(*) FROM flowpilot_idempotency),"
            "(SELECT string_agg(status,',' ORDER BY status) FROM flowpilot_idempotency);",
        ).stdout
        assert after == before
        assert sql(schema, "SELECT COUNT(*) FROM flowpilot_reliability_events;").stdout.strip() == "0"
    finally:
        sql(schema, f'DROP SCHEMA "{schema}" CASCADE;')


def test_lead_insert_generates_one_sanitized_event(db):
    lead_id = seed_lead(db)
    assert sql(
        db,
        "SELECT entity_type,entity_ref,event_type,previous_status IS NULL,new_status IS NULL "
        "FROM flowpilot_reliability_events;",
    ).stdout.strip() == f"lead|{lead_id}|lead_created|t|t"


def test_noop_updates_do_not_duplicate_audit_events(db):
    lead_id = seed_lead(db)
    sql(db, f"UPDATE leads SET draft_status=draft_status WHERE id={lead_id};")
    insert_idempotency(db, "noop-key")
    sql(
        db,
        "UPDATE flowpilot_idempotency SET status=status,workflow_stage=workflow_stage "
        "WHERE idempotency_key='noop-key';",
    )
    counts = sql(
        db,
        "SELECT event_type,COUNT(*) FROM flowpilot_reliability_events "
        "GROUP BY event_type ORDER BY event_type;",
    ).stdout.strip().splitlines()
    assert counts == ["idempotency_claimed|1", "lead_created|1"]


def test_idempotency_stage_and_recovery_events_are_ordered_once(db):
    key = "operator-secret-key"
    insert_idempotency(db, key)
    sql(
        db,
        "UPDATE flowpilot_idempotency SET workflow_stage='business_started',"
        "stage_updated_at=NOW(),updated_at=NOW() WHERE idempotency_key=" + literal(key) + ";",
    )
    sql(
        db,
        "UPDATE flowpilot_idempotency SET status='recovery_required',updated_at=NOW() "
        "WHERE idempotency_key=" + literal(key) + ";",
    )
    events = sql(
        db,
        "SELECT event_type,COALESCE(previous_stage,'null'),COALESCE(new_stage,'null') "
        "FROM flowpilot_reliability_events ORDER BY event_id;",
    ).stdout.strip().splitlines()
    assert events == [
        "idempotency_claimed|null|claimed",
        "idempotency_business_started|claimed|business_started",
        "idempotency_recovery_required|business_started|business_started",
    ]
    ref = sql(
        db,
        "SELECT entity_ref FROM flowpilot_reliability_events "
        "WHERE entity_type='idempotency' LIMIT 1;",
    ).stdout.strip()
    expected = sql(
        db, "SELECT encode(sha256(convert_to(" + literal(key) + ",'UTF8')),'hex');"
    ).stdout.strip()
    assert ref == expected
    assert ref != key and len(ref) == 64


def test_business_complete_and_normal_completion_are_audited(db):
    insert_idempotency(db, "complete-path")
    sql(
        db,
        "UPDATE flowpilot_idempotency SET workflow_stage='business_started',"
        "stage_updated_at=NOW(),updated_at=NOW() WHERE idempotency_key='complete-path';"
        "UPDATE flowpilot_idempotency SET workflow_stage='business_complete',"
        f"response_payload={literal(valid_public_response())}::jsonb,"
        "stage_updated_at=NOW(),updated_at=NOW() WHERE idempotency_key='complete-path';"
        "UPDATE flowpilot_idempotency SET status='completed',completed_at=NOW(),"
        "updated_at=NOW() WHERE idempotency_key='complete-path';",
    )
    assert sql(
        db,
        "SELECT event_type FROM flowpilot_reliability_events "
        "WHERE entity_type='idempotency' ORDER BY event_id;",
    ).stdout.strip().splitlines() == [
        "idempotency_claimed",
        "idempotency_business_started",
        "idempotency_business_complete",
        "idempotency_completed",
    ]


def test_email_followup_and_approval_transitions_are_audited(db):
    approved = seed_lead(db, "-approved")
    rejected = seed_lead(db, "-rejected")
    sql(
        db,
        f"UPDATE leads SET draft_status='pending_approval' WHERE id IN ({approved},{rejected});"
        f"UPDATE leads SET draft_status='approved' WHERE id={approved};"
        f"UPDATE leads SET draft_status='rejected' WHERE id={rejected};"
        f"UPDATE leads SET initial_response_delivery_status='sending' WHERE id={approved};"
        f"UPDATE leads SET initial_response_delivery_status='uncertain' WHERE id={approved};"
        f"UPDATE leads SET followup_status='scheduled' WHERE id={approved};"
        f"UPDATE leads SET followup_status='sending' WHERE id={approved};"
        f"UPDATE leads SET followup_status='uncertain' WHERE id={approved};",
    )
    events = sql(
        db,
        "SELECT event_type FROM flowpilot_reliability_events "
        "WHERE event_type <> 'lead_created' ORDER BY event_id;",
    ).stdout.strip().splitlines()
    assert events == [
        "draft_pending_approval",
        "draft_pending_approval",
        "draft_approved",
        "draft_rejected",
        "initial_email_sending",
        "initial_email_uncertain",
        "followup_scheduled",
        "followup_sending",
        "followup_uncertain",
    ]


def test_email_and_followup_terminal_states_are_audited(db):
    initial_sent = seed_lead(db, "-initial-sent")
    initial_failed = seed_lead(db, "-initial-failed")
    followup_sent = seed_lead(db, "-followup-sent")
    followup_failed = seed_lead(db, "-followup-failed")
    followup_cancelled = seed_lead(db, "-followup-cancelled")
    sql(
        db,
        f"UPDATE leads SET initial_response_delivery_status='sending' WHERE id IN ({initial_sent},{initial_failed});"
        f"UPDATE leads SET initial_response_delivery_status='sent' WHERE id={initial_sent};"
        f"UPDATE leads SET initial_response_delivery_status='failed' WHERE id={initial_failed};"
        f"UPDATE leads SET followup_status='scheduled' WHERE id IN ({followup_sent},{followup_failed},{followup_cancelled});"
        f"UPDATE leads SET followup_status='sending' WHERE id IN ({followup_sent},{followup_failed});"
        f"UPDATE leads SET followup_status='sent' WHERE id={followup_sent};"
        f"UPDATE leads SET followup_status='failed' WHERE id={followup_failed};"
        f"UPDATE leads SET followup_status='cancelled' WHERE id={followup_cancelled};",
    )
    counts = dict(
        line.split("|")
        for line in sql(
            db,
            "SELECT event_type,COUNT(*) FROM flowpilot_reliability_events "
            "WHERE event_type IN ('initial_email_sent','initial_email_failed',"
            "'followup_sent','followup_failed','followup_cancelled') "
            "GROUP BY event_type ORDER BY event_type;",
        ).stdout.strip().splitlines()
    )
    assert counts == {
        "followup_cancelled": "1",
        "followup_failed": "1",
        "followup_sent": "1",
        "initial_email_failed": "1",
        "initial_email_sent": "1",
    }


def test_transaction_rollback_also_rolls_back_audit_event(db):
    result = sql(
        db,
        "BEGIN; INSERT INTO leads "
        "(name,email,company,message,budget,category,priority,lead_score,"
        "short_summary,recommended_action) VALUES "
        "('Rollback','rollback@example.com','Rollback','Rollback',1,'x','low',1,'x','x'); "
        "ROLLBACK; SELECT (SELECT COUNT(*) FROM leads),"
        "(SELECT COUNT(*) FROM flowpilot_reliability_events);",
    )
    assert result.stdout.strip() == "0|0"


def test_concurrent_claimed_to_started_transition_audits_exactly_once(db):
    insert_idempotency(db, "concurrent-key")
    barrier = threading.Barrier(2)

    def transition() -> str:
        barrier.wait(timeout=10)
        return sql(
            db,
            "UPDATE flowpilot_idempotency SET workflow_stage='business_started',"
            "stage_updated_at=NOW(),updated_at=NOW() "
            "WHERE idempotency_key='concurrent-key' AND status='processing' "
            "AND workflow_stage='claimed' RETURNING workflow_stage;",
        ).stdout.strip()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: transition(), range(2)))
    assert sorted(results) == ["", "business_started"]
    assert sql(
        db,
        "SELECT COUNT(*) FROM flowpilot_reliability_events "
        "WHERE event_type='idempotency_business_started';",
    ).stdout.strip() == "1"
    assert sql(
        db,
        "SELECT event_type FROM flowpilot_reliability_events "
        "WHERE entity_type='idempotency' ORDER BY event_id;",
    ).stdout.strip().splitlines() == [
        "idempotency_claimed",
        "idempotency_business_started",
    ]


def test_recovery_records_exact_counters_and_transition_events(db):
    initial = seed_lead(db, "-initial")
    followup = seed_lead(db, "-followup")
    sql(
        db,
        f"UPDATE leads SET initial_response_delivery_status='sending',"
        "initial_response_claim_token=gen_random_uuid(),"
        f"initial_response_claimed_at=NOW()-INTERVAL '31 minutes' WHERE id={initial};"
        f"UPDATE leads SET followup_status='sending',"
        f"followup_claimed_at=NOW()-INTERVAL '31 minutes' WHERE id={followup};",
    )
    insert_idempotency(db, "stale-claimed", age="61 minutes")
    insert_idempotency(db, "stale-started", stage="business_started", age="61 minutes")
    insert_idempotency(
        db,
        "stale-safe-complete",
        stage="business_complete",
        response=valid_public_response(),
        age="61 minutes",
    )
    insert_idempotency(
        db,
        "stale-unsafe-complete",
        stage="business_complete",
        response="{}",
        age="61 minutes",
    )
    insert_idempotency(db, "stale-legacy", stage=None, age="61 minutes")
    assert recover(db) == [1, 1, 1, 1, 1, 1, 1]
    run = sql(
        db,
        "SELECT initial_email_quarantined,followups_quarantined,stale_claimed_failed,"
        "partial_work_recovery_required,safely_completed,"
        "malformed_complete_recovery_required,legacy_recovery_required "
        "FROM flowpilot_recovery_runs;",
    ).stdout.strip()
    assert run == "1|1|1|1|1|1|1"
    events = sql(
        db,
        "SELECT event_type,COUNT(*) FROM flowpilot_reliability_events "
        "WHERE event_type IN ('initial_email_uncertain','followup_uncertain',"
        "'idempotency_failed','idempotency_completed','idempotency_recovery_required') "
        "GROUP BY event_type ORDER BY event_type;",
    ).stdout.strip().splitlines()
    assert events == [
        "followup_uncertain|1",
        "idempotency_completed|1",
        "idempotency_failed|1",
        "idempotency_recovery_required|3",
        "initial_email_uncertain|1",
    ]


def test_zero_change_recovery_still_records_one_summary(db):
    assert recover(db) == [0, 0, 0, 0, 0, 0, 0]
    assert recover(db) == [0, 0, 0, 0, 0, 0, 0]
    assert sql(
        db,
        "SELECT COUNT(*),SUM(initial_email_quarantined+followups_quarantined+"
        "stale_claimed_failed+partial_work_recovery_required+safely_completed+"
        "malformed_complete_recovery_required+legacy_recovery_required) "
        "FROM flowpilot_recovery_runs;",
    ).stdout.strip() == "2|0"


def test_reconciliation_queue_contains_only_attention_items(db):
    initial = seed_lead(db, "-queue-initial")
    followup = seed_lead(db, "-queue-followup")
    normal = seed_lead(db, "-normal")
    sql(
        db,
        f"UPDATE leads SET initial_response_delivery_status='uncertain' WHERE id={initial};"
        f"UPDATE leads SET followup_status='uncertain' WHERE id={followup};"
        f"UPDATE leads SET initial_response_delivery_status='sent',followup_status='sent' "
        f"WHERE id={normal};",
    )
    insert_idempotency(db, "queue-recovery", status="recovery_required", stage="business_started")
    insert_idempotency(
        db,
        "queue-completed",
        status="completed",
        stage="business_complete",
        response=valid_public_response(),
    )
    insert_idempotency(db, "queue-failed", status="failed", stage="claimed")
    rows = sql(
        db,
        "SELECT item_type,display_status,technical_status,requires_action,age_seconds>=0 "
        "FROM flowpilot_reconciliation_queue ORDER BY item_type;",
    ).stdout.strip().splitlines()
    assert rows == [
        "followup_email|Follow-up delivery uncertain|uncertain|t|t",
        "idempotency_request|Needs review|recovery_required|t|t",
        "initial_email|Initial email delivery uncertain|uncertain|t|t",
    ]


def test_views_and_events_do_not_expose_sensitive_content(db):
    lead_id = seed_lead(db, "-privacy")
    token = "00000000-0000-4000-8000-000000000123"
    sql(
        db,
        f"UPDATE leads SET draft_subject='Secret subject',draft_body='Secret body',"
        f"approval_token='{token}',initial_response_delivery_status='uncertain' "
        f"WHERE id={lead_id};",
    )
    key = "plaintext-idempotency-key-must-not-leak"
    insert_idempotency(db, key, status="recovery_required", stage="business_started")
    exposed = sql(
        db,
        "SELECT COALESCE(string_agg(row_to_json(x)::text,E'\\n'),'') FROM ("
        "SELECT * FROM flowpilot_reliability_events UNION ALL "
        "SELECT NULL::bigint,NULL::timestamptz,item_type,item_ref,display_status,"
        "technical_status,technical_stage,NULL::text,NULL::text "
        "FROM flowpilot_reconciliation_queue) AS x;",
    ).stdout
    for secret in (
        "Sensitive Name",
        "private-privacy@example.com",
        "Secret Company",
        "Private customer message",
        "Secret subject",
        "Secret body",
        token,
        key,
    ):
        assert secret not in exposed


def test_reliability_summary_reports_current_operational_counts(db):
    lead_id = seed_lead(db, "-summary")
    sql(
        db,
        f"UPDATE leads SET initial_response_delivery_status='uncertain',"
        f"followup_status='uncertain' WHERE id={lead_id};",
    )
    insert_idempotency(db, "summary-recovery", status="recovery_required", stage="business_started")
    insert_idempotency(db, "summary-processing", status="processing", stage="claimed")
    recover(db)
    summary = sql(
        db,
        "SELECT items_needing_reconciliation,uncertain_initial_emails,"
        "uncertain_followups,processing_idempotency_records,"
        "latest_recovery_run_at IS NOT NULL FROM flowpilot_reliability_summary;",
    ).stdout.strip()
    assert summary == "3|1|1|1|t"
