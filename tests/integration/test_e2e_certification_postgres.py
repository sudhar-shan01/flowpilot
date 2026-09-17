"""Optional PostgreSQL 17 certification tests for the complete durable lifecycle.

The tests use only synthetic data and the SQL exported in the four committed
n8n workflows. Set ``FLOWPILOT_TEST_PG_CONTAINER`` to a disposable PostgreSQL
17 container name to enable them.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import threading
from time import perf_counter
import uuid

import pytest


ROOT = Path(__file__).parents[2]
CONTAINER = os.getenv("FLOWPILOT_TEST_PG_CONTAINER")
pytestmark = pytest.mark.skipif(
    not CONTAINER,
    reason="Optional disposable PostgreSQL certification verification",
)


def load_nodes(name: str) -> dict[str, dict[str, object]]:
    workflow = json.loads((ROOT / "n8n" / name).read_text(encoding="utf-8"))
    return {node["name"]: node for node in workflow["nodes"]}


LEAD = load_nodes("flowpilot-lead-workflow.json")
APPROVAL = load_nodes("flowpilot-approval-workflow.json")
FOLLOWUP = load_nodes("flowpilot-followup-workflow.json")

CLAIM_IDEMPOTENCY = LEAD["Atomically Claim Idempotency Key"]["parameters"][
    "query"
].rstrip(";")
START_BUSINESS = LEAD["Mark Idempotency Business Started"]["parameters"][
    "query"
].rstrip(";")
STORE_RESPONSE = LEAD["Record Idempotency Business Complete"]["parameters"][
    "query"
].rstrip(";")
COMPLETE_IDEMPOTENCY = LEAD["Mark Idempotency Completed"]["parameters"][
    "query"
].rstrip(";")
INSERT_LEAD = LEAD["Persist Lead in PostgreSQL"]["parameters"]["query"].rstrip(";")
PERSIST_DRAFT = LEAD["Persist Draft in PostgreSQL"]["parameters"]["query"].rstrip(
    ";"
)
ISSUE_APPROVAL = LEAD["Create Approval Token"]["parameters"]["query"].rstrip(";")
AUTHORIZE = APPROVAL["Atomically Authorize Decision"]["parameters"]["query"].rstrip(
    ";"
)
CLAIM_INITIAL = APPROVAL["Atomically Claim Initial Response"]["parameters"][
    "query"
].rstrip(";")
MARK_INITIAL_SENT = APPROVAL["Record Response Sent Timestamp"]["parameters"][
    "query"
].rstrip(";")
CLAIM_FOLLOWUP = FOLLOWUP["Atomically Claim Due Follow-ups"]["parameters"][
    "query"
].rstrip(";")
MARK_FOLLOWUP_SENT = FOLLOWUP["Mark Follow-up Sent"]["parameters"][
    "query"
].rstrip(";")


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


def execute(script: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command(),
        input=script,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


def sql(schema: str, query: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return execute(f'SET search_path TO "{schema}";\n{query}', check=check)


def literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def apply_migrations(schema: str) -> None:
    for migration in sorted((ROOT / "postgres" / "init").glob("*.sql")):
        sql(schema, migration.read_text(encoding="utf-8"))


@pytest.fixture
def db():
    schema = "fp_cert_" + uuid.uuid4().hex
    sql(schema, f'CREATE SCHEMA "{schema}";')
    apply_migrations(schema)
    yield schema
    sql(schema, f'DROP SCHEMA "{schema}" CASCADE;')


def parse_row(output: str, fields: tuple[str, ...]) -> dict[str, str]:
    values = output.strip().split("|")
    assert len(values) == len(fields), output
    return dict(zip(fields, values, strict=True))


def claim_idempotency(schema: str, key: str, payload: str) -> dict[str, str]:
    output = sql(
        schema,
        "PREPARE fp_claim(text,text) AS "
        + CLAIM_IDEMPOTENCY
        + "; EXECUTE fp_claim("
        + literal(key)
        + ","
        + literal(payload)
        + ");",
    ).stdout
    return parse_row(
        output,
        ("outcome", "request_fingerprint", "claim_token", "response_payload"),
    )


def start_business(schema: str, key: str, claim: dict[str, str]) -> None:
    output = sql(
        schema,
        "PREPARE fp_start(text,text,uuid) AS "
        + START_BUSINESS
        + "; EXECUTE fp_start("
        + ",".join(
            (
                literal(key),
                literal(claim["request_fingerprint"]),
                literal(claim["claim_token"]),
            )
        )
        + ");",
    ).stdout.strip()
    assert output == "t"


def insert_lead(schema: str, suffix: str = "") -> int:
    values = (
        literal("Synthetic Lead" + suffix),
        literal(f"synthetic{suffix}@example.test"),
        literal("Synthetic Company"),
        literal("Please automate our deterministic test workflow."),
        "5000",
        literal("workflow automation"),
        literal("high"),
        "88",
        literal("Synthetic lead needs workflow automation."),
        literal("Schedule a synthetic discovery call."),
    )
    output = sql(
        schema,
        "PREPARE fp_insert AS "
        + INSERT_LEAD
        + "; EXECUTE fp_insert("
        + ",".join(values)
        + ");",
    ).stdout.strip()
    assert output
    return int(output.split("|", 1)[0])


def persist_draft_and_issue_token(schema: str, lead_id: int) -> str:
    subject = "Synthetic workflow response"
    body = "Hello, this is a deterministic synthetic response draft."
    output = sql(
        schema,
        "PREPARE fp_draft AS "
        + PERSIST_DRAFT
        + "; EXECUTE fp_draft("
        + ",".join(
            (literal(subject), literal(body), literal("pending_approval"), str(lead_id))
        )
        + "); PREPARE fp_issue AS "
        + ISSUE_APPROVAL
        + f"; EXECUTE fp_issue({lead_id});",
    ).stdout.strip().splitlines()
    assert output[0].split("|")[-1] == "pending_approval"
    issued = output[-1].split("|")
    assert issued[0] == "t" and int(issued[1]) == lead_id
    return issued[2]


def finalize_idempotency(
    schema: str,
    key: str,
    claim: dict[str, str],
) -> None:
    response = json.dumps(
        {
            "success": True,
            "route": "high",
            "message": "High-priority lead received.",
            "analysis": {
                "category": "workflow automation",
                "priority": "high",
                "lead_score": 88,
                "short_summary": "Synthetic lead needs workflow automation.",
                "recommended_action": "Schedule a synthetic discovery call.",
            },
        },
        separators=(",", ":"),
    )
    owner = ",".join(
        (
            literal(key),
            literal(claim["request_fingerprint"]),
            literal(claim["claim_token"]),
        )
    )
    output = sql(
        schema,
        "PREPARE fp_store(text,text,uuid,jsonb) AS "
        + STORE_RESPONSE
        + f"; EXECUTE fp_store({owner},{literal(response)});"
        + " PREPARE fp_complete(text,text,uuid) AS "
        + COMPLETE_IDEMPOTENCY
        + f"; EXECUTE fp_complete({owner});",
    ).stdout.strip().splitlines()
    assert output == ["t", "t"]


def authorize(schema: str, lead_id: int, token: str, decision: str) -> dict[str, str]:
    output = sql(
        schema,
        "PREPARE fp_authorize(bigint,uuid,text) AS "
        + AUTHORIZE
        + "; EXECUTE fp_authorize("
        + ",".join((str(lead_id), literal(token), literal(decision)))
        + ");",
    ).stdout
    return parse_row(
        output,
        (
            "authorized",
            "id",
            "email",
            "draft_subject",
            "draft_body",
            "draft_status",
            "approval_decided_at",
        ),
    )


def claim_initial(schema: str, lead_id: int) -> dict[str, str]:
    output = sql(
        schema,
        "PREPARE fp_initial(bigint) AS "
        + CLAIM_INITIAL
        + f"; EXECUTE fp_initial({lead_id});",
    ).stdout
    return parse_row(
        output,
        (
            "outcome",
            "lead_id",
            "email",
            "draft_subject",
            "draft_body",
            "claim_token",
            "claimed_at",
        ),
    )


def mark_initial_sent(schema: str, lead_id: int, claim_token: str) -> None:
    output = sql(
        schema,
        "PREPARE fp_initial_sent(bigint,uuid) AS "
        + MARK_INITIAL_SENT
        + "; EXECUTE fp_initial_sent("
        + f"{lead_id},{literal(claim_token)});",
    ).stdout.strip()
    assert output.split("|")[:2] == ["t", "sent"]


def test_complete_approved_lifecycle_has_one_send_and_clean_queue(db):
    key = "certification-happy-path"
    payload = '[["name","Synthetic Lead"],["budget",5000]]'
    idempotency = claim_idempotency(db, key, payload)
    assert idempotency["outcome"] == "owner"
    start_business(db, key, idempotency)

    lead_id = insert_lead(db)
    token = persist_draft_and_issue_token(db, lead_id)
    # Sheets, HubSpot, AI draft, and notification are deterministic test doubles;
    # the durable boundary is completed only after those doubles report success.
    finalize_idempotency(db, key, idempotency)

    approval = authorize(db, lead_id, token, "approve")
    assert approval["authorized"] == "t"
    assert approval["draft_status"] == "approved"
    initial = claim_initial(db, lead_id)
    assert initial["outcome"] == "owner"
    assert initial["email"] == "synthetic@example.test"
    mark_initial_sent(db, lead_id, initial["claim_token"])

    schedule = sql(
        db,
        "SELECT followup_status,"
        "EXTRACT(EPOCH FROM (followup_due_at-initial_response_sent_at))::integer "
        f"FROM leads WHERE id={lead_id};",
    ).stdout.strip()
    assert schedule == "scheduled|259200"

    sql(
        db,
        "UPDATE leads SET initial_response_sent_at=NOW()-INTERVAL '73 hours',"
        "followup_due_at=NOW()-INTERVAL '1 hour' "
        f"WHERE id={lead_id};",
    )
    claimed = parse_row(
        sql(db, CLAIM_FOLLOWUP + ";").stdout,
        ("lead_id", "name", "email", "claim_time"),
    )
    assert int(claimed["lead_id"]) == lead_id
    assert claimed["email"] == "synthetic@example.test"
    marked = sql(
        db,
        "PREPARE fp_followup_sent(bigint,timestamptz) AS "
        + MARK_FOLLOWUP_SENT
        + "; EXECUTE fp_followup_sent("
        + f"{lead_id},{literal(claimed['claim_time'])});",
    ).stdout.strip()
    assert marked == "t|sent"

    assert sql(
        db,
        f"SELECT initial_response_delivery_status,followup_status,"
        f"initial_response_sent_at IS NOT NULL,followup_sent_at IS NOT NULL "
        f"FROM leads WHERE id={lead_id};",
    ).stdout.strip() == "sent|sent|t|t"
    assert sql(
        db,
        "SELECT status,workflow_stage FROM flowpilot_idempotency "
        f"WHERE idempotency_key={literal(key)};",
    ).stdout.strip() == "completed|business_complete"
    assert sql(db, "SELECT COUNT(*) FROM flowpilot_reconciliation_queue;").stdout.strip() == "0"
    events = sql(
        db,
        "SELECT event_type FROM flowpilot_reliability_events ORDER BY event_id;",
    ).stdout.strip().splitlines()
    assert events == [
        "idempotency_claimed",
        "idempotency_business_started",
        "lead_created",
        "draft_pending_approval",
        "idempotency_business_complete",
        "idempotency_completed",
        "draft_approved",
        "initial_email_sending",
        "initial_email_sent",
        "followup_scheduled",
        "followup_sending",
        "followup_sent",
    ]


def test_complete_reject_lifecycle_never_claims_customer_email_or_followup(db):
    lead_id = insert_lead(db, "-reject")
    token = persist_draft_and_issue_token(db, lead_id)

    rejection = authorize(db, lead_id, token, "reject")
    assert rejection["authorized"] == "t"
    assert rejection["draft_status"] == "rejected"
    initial = claim_initial(db, lead_id)
    assert initial["outcome"] == "blocked"
    assert initial["claim_token"] == ""
    assert sql(db, CLAIM_FOLLOWUP + ";").stdout.strip() == ""
    assert sql(
        db,
        f"SELECT draft_status,approval_token IS NULL,"
        "initial_response_delivery_status IS NULL,followup_status IS NULL "
        f"FROM leads WHERE id={lead_id};",
    ).stdout.strip() == "rejected|t|t|t"
    assert sql(db, "SELECT COUNT(*) FROM flowpilot_reconciliation_queue;").stdout.strip() == "0"


def test_concurrent_approval_double_click_allows_exactly_one_decision(db):
    lead_id = insert_lead(db, "-approval-race")
    token = persist_draft_and_issue_token(db, lead_id)
    barrier = threading.Barrier(2)

    def decide(decision: str) -> dict[str, str]:
        barrier.wait(timeout=10)
        return authorize(db, lead_id, token, decision)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ("approve", "reject")))

    assert sorted(row["authorized"] for row in results) == ["f", "t"]
    state = sql(
        db,
        f"SELECT draft_status,approval_token IS NULL,approval_decided_at IS NOT NULL "
        f"FROM leads WHERE id={lead_id};",
    ).stdout.strip().split("|")
    assert state[0] in {"approved", "rejected"}
    assert state[1:] == ["t", "t"]
    assert sql(
        db,
        "SELECT COUNT(*) FROM flowpilot_reliability_events "
        "WHERE event_type IN ('draft_approved','draft_rejected');",
    ).stdout.strip() == "1"


@pytest.mark.parametrize("request_count", [1, 10, 25, 50])
def test_concurrent_idempotency_burst_has_one_owner_per_unique_key(
    db,
    request_count: int,
    record_property,
):
    duplicate_count = 0 if request_count == 1 else max(1, request_count // 5)
    unique_count = request_count - duplicate_count
    keys = [f"burst-{request_count}-{index}" for index in range(unique_count)]
    keys.extend(keys[index % unique_count] for index in range(duplicate_count))
    barrier = threading.Barrier(request_count)

    def claim(key: str) -> str:
        barrier.wait(timeout=20)
        return claim_idempotency(db, key, '[["name","Burst Synthetic"]]')[
            "outcome"
        ]

    started = perf_counter()
    with ThreadPoolExecutor(max_workers=request_count) as pool:
        outcomes = list(pool.map(claim, keys))
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    record_property("burst_elapsed_ms", elapsed_ms)

    assert outcomes.count("owner") == unique_count
    assert outcomes.count("in_progress") == duplicate_count
    assert set(outcomes) <= {"owner", "in_progress"}
    counts = sql(
        db,
        "SELECT COUNT(*),"
        "COUNT(*) FILTER (WHERE status='recovery_required') "
        "FROM flowpilot_idempotency;",
    ).stdout.strip()
    assert counts == f"{unique_count}|0"
    assert sql(
        db,
        "SELECT COUNT(*) FROM leads WHERE initial_response_delivery_status='uncertain' "
        "OR followup_status='uncertain';",
    ).stdout.strip() == "0"


def test_concurrent_synthetic_lead_submissions_do_not_duplicate_side_effects(
    db,
    record_property,
):
    request_count = 25
    duplicate_count = 5
    unique_count = request_count - duplicate_count
    keys = [f"full-burst-{index}" for index in range(unique_count)]
    keys.extend(keys[index] for index in range(duplicate_count))
    barrier = threading.Barrier(request_count)
    counter_lock = threading.Lock()
    side_effects = {
        "analysis": 0,
        "sheets": 0,
        "hubspot": 0,
        "draft": 0,
        "approval_notification": 0,
    }

    def record(stage: str) -> None:
        with counter_lock:
            side_effects[stage] += 1

    def submit(key: str) -> tuple[str, float]:
        barrier.wait(timeout=20)
        started = perf_counter()
        claim = claim_idempotency(
            db,
            key,
            '[["name","Synthetic Lead"],["budget",5000]]',
        )
        if claim["outcome"] == "owner":
            record("analysis")
            start_business(db, key, claim)
            lead_id = insert_lead(db, "-" + key)
            record("sheets")
            record("hubspot")
            record("draft")
            persist_draft_and_issue_token(db, lead_id)
            record("approval_notification")
            finalize_idempotency(db, key, claim)
        return claim["outcome"], (perf_counter() - started) * 1000

    with ThreadPoolExecutor(max_workers=request_count) as pool:
        results = list(pool.map(submit, keys))
    outcomes = [result[0] for result in results]
    latencies = sorted(result[1] for result in results)

    assert outcomes.count("owner") == unique_count
    assert set(outcomes) <= {"owner", "in_progress", "completed"}
    assert side_effects == {stage: unique_count for stage in side_effects}
    assert sql(db, "SELECT COUNT(*) FROM leads;").stdout.strip() == str(unique_count)
    assert sql(
        db,
        "SELECT COUNT(*) FROM flowpilot_idempotency WHERE status='completed';",
    ).stdout.strip() == str(unique_count)
    assert sql(
        db,
        "SELECT COUNT(*) FROM flowpilot_idempotency WHERE status='recovery_required';",
    ).stdout.strip() == "0"
    assert sql(
        db,
        "SELECT COUNT(*) FROM leads WHERE initial_response_delivery_status='uncertain' "
        "OR followup_status='uncertain';",
    ).stdout.strip() == "0"
    record_property("unique_successes", unique_count)
    record_property("duplicate_requests", duplicate_count)
    record_property("latency_p50_ms", round(latencies[len(latencies) // 2], 2))
    record_property(
        "latency_p95_ms",
        round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 2),
    )
    record_property("latency_max_ms", round(latencies[-1], 2))


def test_concurrent_migration_runners_are_serialized_and_rerunnable():
    schema = "fp_cert_migration_" + uuid.uuid4().hex
    sql(schema, f'CREATE SCHEMA "{schema}";')
    migrations = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "postgres" / "init").glob("*.sql"))
    )
    run_migrations = (ROOT / "run-migrations.sql").read_text(encoding="utf-8")
    assert "pg_advisory_lock(hashtextextended('flowpilot-schema-migrations', 0))" in run_migrations
    assert "pg_advisory_unlock(hashtextextended('flowpilot-schema-migrations', 0))" in run_migrations

    lock_name = "flowpilot-schema-migrations"
    script = (
        f'SET search_path TO "{schema}";\n'
        f"SELECT pg_advisory_lock(hashtextextended({literal(lock_name)},0));\n"
        + migrations
        + f"\nSELECT pg_advisory_unlock(hashtextextended({literal(lock_name)},0));\n"
    )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: execute(script), range(2)))
        assert all(result.returncode == 0 for result in results)
        assert sql(
            schema,
            "SELECT COUNT(*) FROM pg_trigger WHERE tgname IN "
            "('flowpilot_audit_lead_change_trigger',"
            "'flowpilot_audit_idempotency_change_trigger') AND NOT tgisinternal;",
        ).stdout.strip() == "2"
        assert sql(
            schema,
            "SELECT COUNT(*) FROM pg_indexes WHERE schemaname=current_schema() "
            "AND indexname IN ('leads_followup_due_index',"
            "'flowpilot_idempotency_processing_stage_index',"
            "'flowpilot_reliability_events_entity_index');",
        ).stdout.strip() == "3"
    finally:
        sql(schema, f'DROP SCHEMA "{schema}" CASCADE;')


def test_operational_due_followup_query_uses_partial_index(db):
    sql(
        db,
        "INSERT INTO leads "
        "(name,email,company,message,budget,category,priority,lead_score,"
        "short_summary,recommended_action) "
        "SELECT 'Load '||value,'load'||value||'@example.test','Synthetic',"
        "'Synthetic performance certification message',1,'automation','low',10,"
        "'Synthetic','None' FROM generate_series(1,5000) AS value;"
        "UPDATE leads SET draft_status='approved',initial_response_sent_at=NOW()-INTERVAL '73 hours',"
        "initial_response_delivery_status='sent',followup_status='scheduled',"
        "followup_due_at=NOW()-INTERVAL '1 hour' WHERE id IN "
        "(SELECT id FROM leads ORDER BY id LIMIT 10); ANALYZE leads;",
    )
    plan = sql(
        db,
        "EXPLAIN (COSTS OFF) SELECT id FROM leads "
        "WHERE followup_status='scheduled' AND followup_due_at<=NOW() "
        "AND followup_due_at>=initial_response_sent_at+INTERVAL '72 hours' "
        "AND draft_status='approved' AND initial_response_sent_at IS NOT NULL "
        "AND followup_sent_at IS NULL ORDER BY followup_due_at,id LIMIT 20 "
        "FOR UPDATE SKIP LOCKED;",
    ).stdout
    assert "leads_followup_due_index" in plan
