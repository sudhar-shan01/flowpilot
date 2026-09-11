"""Optional PostgreSQL 17 execution tests for Phase 8B1 recovery SQL.

Set FLOWPILOT_TEST_PG_CONTAINER to a fresh disposable postgres:17 container.
The tests use docker exec over the local socket and no project credentials.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest


ROOT = Path(__file__).parents[2]
CONTAINER = os.getenv("FLOWPILOT_TEST_PG_CONTAINER")
pytestmark = pytest.mark.skipif(
    not CONTAINER, reason="Optional disposable PostgreSQL verification"
)
APPROVAL = json.loads(
    (ROOT / "n8n/flowpilot-approval-workflow.json").read_text()
)
FOLLOWUP = json.loads(
    (ROOT / "n8n/flowpilot-followup-workflow.json").read_text()
)
RECOVERY = json.loads(
    (ROOT / "n8n/flowpilot-recovery-workflow.json").read_text()
)
APPROVAL_NODES = {node["name"]: node for node in APPROVAL["nodes"]}
FOLLOWUP_NODES = {node["name"]: node for node in FOLLOWUP["nodes"]}
RECOVERY_NODES = {node["name"]: node for node in RECOVERY["nodes"]}
INITIAL_CLAIM = APPROVAL_NODES["Atomically Claim Initial Response"]["parameters"][
    "query"
].rstrip(";")
INITIAL_SENT = APPROVAL_NODES["Record Response Sent Timestamp"]["parameters"][
    "query"
].rstrip(";")
INITIAL_FAILED = APPROVAL_NODES["Mark Initial Response Failed"]["parameters"][
    "query"
].rstrip(";")
INITIAL_UNCERTAIN = APPROVAL_NODES["Mark Initial Response Uncertain"]["parameters"][
    "query"
].rstrip(";")
FOLLOWUP_CLAIM = FOLLOWUP_NODES["Atomically Claim Due Follow-ups"]["parameters"][
    "query"
].rstrip(";")
FOLLOWUP_UNCERTAIN = FOLLOWUP_NODES["Mark Follow-up Uncertain"]["parameters"][
    "query"
].rstrip(";")
RECOVERY_SWEEP = RECOVERY_NODES["Reconcile Stale Work"]["parameters"][
    "query"
].rstrip(";")


def command():
    return [
        "docker",
        "exec",
        "-i",
        CONTAINER,
        "psql",
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
    ]


def sql(schema, query, check=True):
    result = subprocess.run(
        command(),
        input=f'SET search_path TO "{schema}";\n' + query,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def db():
    schema = "fp8b1_" + uuid.uuid4().hex
    sql(schema, f'CREATE SCHEMA "{schema}";')
    for migration in sorted((ROOT / "postgres/init").glob("*.sql")):
        sql(schema, migration.read_text())
    yield schema
    sql(schema, f'DROP SCHEMA "{schema}" CASCADE;')


def seed_initial(db, count=1, status="approved"):
    sql(
        db,
        f"""INSERT INTO leads
        (name,email,company,message,budget,category,priority,lead_score,
         short_summary,recommended_action,draft_subject,draft_body,draft_status)
        SELECT 'Example','lead' || n || '@example.com','Example','Test',100,
               'automation','high',80,'Test','Reply','Subject','Body','{status}'
        FROM generate_series(1,{count}) AS n;""",
    )


def execute_initial_claim(db, lead_id=1):
    result = sql(
        db,
        "PREPARE initial_claim(bigint) AS "
        + INITIAL_CLAIM
        + f"; EXECUTE initial_claim({lead_id});",
    )
    fields = result.stdout.strip().split("|")
    return {
        "outcome": fields[0],
        "lead_id": fields[1] if len(fields) > 1 else "",
        "claim_token": fields[5] if len(fields) > 5 else "",
        "claimed_at": fields[6] if len(fields) > 6 else "",
    }


def execute_initial_state(db, query, lead_id, token):
    return sql(
        db,
        "PREPARE initial_state(bigint,uuid) AS "
        + query
        + f"; EXECUTE initial_state({lead_id},'{token}');",
    ).stdout.strip()


def claim_followups(db):
    result = sql(
        db,
        f"WITH claimed AS ({FOLLOWUP_CLAIM}) "
        "SELECT COALESCE(json_agg(claimed),'[]') FROM claimed;",
    )
    return json.loads(result.stdout)


def test_migration_reruns_preserve_rows_and_constraints(db):
    seed_initial(db)
    before = sql(db, "SELECT row_to_json(leads) FROM leads;").stdout
    migration = (ROOT / "postgres/init/006_add_email_recovery.sql").read_text()
    sql(db, migration)
    sql(db, migration)
    assert sql(db, "SELECT row_to_json(leads) FROM leads;").stdout == before
    assert sql(
        db,
        "SELECT initial_response_delivery_status IS NULL, followup_status IS NULL FROM leads;",
    ).stdout.strip() == "t|t"
    initial_invalid = sql(
        db, "UPDATE leads SET initial_response_delivery_status='invalid';", check=False
    )
    followup_invalid = sql(
        db, "UPDATE leads SET followup_status='invalid';", check=False
    )
    assert initial_invalid.returncode != 0
    assert "leads_initial_response_delivery_status_check" in initial_invalid.stderr
    assert followup_invalid.returncode != 0
    assert "leads_followup_status_check" in followup_invalid.stderr


def test_initial_claim_requires_approved_unsent_not_attempted_and_valid_draft(db):
    seed_initial(db, 7)
    sql(db, "UPDATE leads SET draft_status='pending_approval' WHERE id=2;")
    sql(db, "UPDATE leads SET draft_status='rejected' WHERE id=3;")
    sql(db, "UPDATE leads SET initial_response_sent_at=NOW() WHERE id=4;")
    sql(db, "UPDATE leads SET initial_response_delivery_status='sending' WHERE id=5;")
    sql(db, "UPDATE leads SET initial_response_delivery_status='uncertain' WHERE id=6;")
    sql(db, "UPDATE leads SET email='invalid' WHERE id=7;")
    assert execute_initial_claim(db, 1)["outcome"] == "owner"
    for lead_id in (2, 3, 4, 5, 6):
        assert execute_initial_claim(db, lead_id)["outcome"] == "blocked"
    assert execute_initial_claim(db, 7)["outcome"] == "failed"
    assert sql(
        db,
        "SELECT initial_response_delivery_status FROM leads WHERE id=7;",
    ).stdout.strip() == "failed"


def test_concurrent_initial_claims_have_exactly_one_owner(db):
    seed_initial(db)
    process = subprocess.Popen(
        command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        process.stdin.write(
            f'SET search_path TO "{db}"; BEGIN;\n'
            f"PREPARE initial_claim(bigint) AS {INITIAL_CLAIM};\n"
            "EXECUTE initial_claim(1);\n"
        )
        process.stdin.flush()
        first = process.stdout.readline().split("|", 1)[0]
        assert first == "owner"
        with ThreadPoolExecutor(max_workers=1) as executor:
            second_future = executor.submit(execute_initial_claim, db, 1)
            process.stdin.write("COMMIT;\n")
            process.stdin.flush()
            process.stdin.close()
            process.wait(timeout=10)
            second = second_future.result(timeout=10)
        assert process.returncode == 0, process.stderr.read()
        assert second["outcome"] == "blocked"
        assert sql(
            db,
            "SELECT initial_response_delivery_status,initial_response_claim_token IS NOT NULL FROM leads;",
        ).stdout.strip() == "sending|t"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_claim_token_guards_sent_transition_and_schedules_exactly_72_hours(db):
    seed_initial(db)
    claim = execute_initial_claim(db)
    wrong = "00000000-0000-4000-8000-000000000099"
    assert execute_initial_state(db, INITIAL_SENT, 1, wrong).split("|")[0] == "f"
    assert sql(
        db,
        "SELECT initial_response_delivery_status,initial_response_sent_at IS NULL FROM leads;",
    ).stdout.strip() == "sending|t"
    assert execute_initial_state(
        db, INITIAL_SENT, 1, claim["claim_token"]
    ).split("|")[0] == "t"
    assert sql(
        db,
        "SELECT initial_response_delivery_status,initial_response_sent_at IS NOT NULL,"
        "followup_status,EXTRACT(EPOCH FROM (followup_due_at-initial_response_sent_at)) "
        "FROM leads;",
    ).stdout.strip() == "sent|t|scheduled|259200.000000"
    assert execute_initial_claim(db)["outcome"] == "blocked"


@pytest.mark.parametrize(
    ("query", "state"),
    [(INITIAL_FAILED, "failed"), (INITIAL_UNCERTAIN, "uncertain")],
)
def test_initial_non_sent_results_require_matching_original_claim(db, query, state):
    seed_initial(db)
    claim = execute_initial_claim(db)
    wrong = "00000000-0000-4000-8000-000000000099"
    assert execute_initial_state(db, query, 1, wrong).split("|")[0] == "f"
    assert execute_initial_state(
        db, query, 1, claim["claim_token"]
    ).split("|")[0] == "t"
    assert sql(
        db,
        "SELECT initial_response_delivery_status,initial_response_sent_at IS NULL,"
        "followup_status IS NULL FROM leads;",
    ).stdout.strip() == f"{state}|t|t"
    assert execute_initial_claim(db)["outcome"] == "blocked"


def test_recovery_quarantines_only_stale_initial_and_followup_sends(db):
    seed_initial(db, 10)
    token = "00000000-0000-4000-8000-000000000001"
    sql(
        db,
        f"""UPDATE leads SET initial_response_delivery_status='sending',
          initial_response_claim_token='{token}',initial_response_claimed_at=NOW()-INTERVAL '31 minutes' WHERE id=1;
        UPDATE leads SET initial_response_delivery_status='sending',
          initial_response_claim_token='{token}',initial_response_claimed_at=NOW()-INTERVAL '29 minutes' WHERE id=2;
        UPDATE leads SET initial_response_delivery_status='sent',initial_response_claimed_at=NOW()-INTERVAL '31 minutes' WHERE id=3;
        UPDATE leads SET initial_response_delivery_status='failed',initial_response_claimed_at=NOW()-INTERVAL '31 minutes' WHERE id=4;
        UPDATE leads SET initial_response_delivery_status='uncertain',initial_response_claimed_at=NOW()-INTERVAL '31 minutes' WHERE id=5;
        UPDATE leads SET initial_response_sent_at=NOW()-INTERVAL '73 hours',followup_status='sending',followup_claimed_at=NOW()-INTERVAL '31 minutes' WHERE id=6;
        UPDATE leads SET initial_response_sent_at=NOW()-INTERVAL '73 hours',followup_status='sending',followup_claimed_at=NOW()-INTERVAL '29 minutes' WHERE id=7;
        UPDATE leads SET followup_status='sent',followup_claimed_at=NOW()-INTERVAL '31 minutes' WHERE id=8;
        UPDATE leads SET followup_status='failed',followup_claimed_at=NOW()-INTERVAL '31 minutes' WHERE id=9;
        UPDATE leads SET followup_status='cancelled',followup_claimed_at=NOW()-INTERVAL '31 minutes' WHERE id=10;""",
    )
    assert sql(db, RECOVERY_SWEEP).stdout.strip().split("|")[:2] == ["1", "1"]
    assert sql(
        db,
        "SELECT string_agg(id || ':' || COALESCE(initial_response_delivery_status,'null'),',' ORDER BY id) FROM leads WHERE id<=5;",
    ).stdout.strip() == "1:uncertain,2:sending,3:sent,4:failed,5:uncertain"
    assert sql(
        db,
        "SELECT string_agg(id || ':' || COALESCE(followup_status,'null'),',' ORDER BY id) FROM leads WHERE id>=6;",
    ).stdout.strip() == "6:uncertain,7:sending,8:sent,9:failed,10:cancelled"


def test_recovery_includes_exact_stale_boundary_without_sleep(db):
    seed_initial(db)
    token = "00000000-0000-4000-8000-000000000001"
    result = sql(
        db,
        f"""BEGIN;
        UPDATE leads SET initial_response_delivery_status='sending',
          initial_response_claim_token='{token}',
          initial_response_claimed_at=NOW()-INTERVAL '30 minutes';
        {RECOVERY_SWEEP};
        SELECT initial_response_delivery_status FROM leads;
        COMMIT;""",
    )
    assert result.stdout.splitlines()[-1] == "uncertain"


def test_uncertain_followup_cannot_be_reclaimed_and_guard_requires_original_claim(db):
    seed_initial(db)
    sql(
        db,
        "UPDATE leads SET initial_response_sent_at=NOW()-INTERVAL '73 hours',"
        "followup_status='scheduled',followup_due_at=NOW()-INTERVAL '1 hour';",
    )
    row = claim_followups(db)[0]
    wrong_time = "2000-01-01T00:00:00+00"
    wrong = sql(
        db,
        "PREPARE mark(bigint,timestamptz) AS "
        + FOLLOWUP_UNCERTAIN
        + f"; EXECUTE mark(1,'{wrong_time}');",
    ).stdout.strip()
    assert wrong.split("|")[0] == "f"
    correct = sql(
        db,
        "PREPARE mark(bigint,timestamptz) AS "
        + FOLLOWUP_UNCERTAIN
        + f"; EXECUTE mark(1,'{row['claim_time']}');",
    ).stdout.strip()
    assert correct.split("|")[0] == "t"
    assert sql(db, "SELECT followup_status FROM leads;").stdout.strip() == "uncertain"
    assert claim_followups(db) == []
