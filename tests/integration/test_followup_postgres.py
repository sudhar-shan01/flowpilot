"""Optional execution tests for the actual committed SQL on disposable PostgreSQL.

Set FLOWPILOT_TEST_PG_CONTAINER to an isolated postgres:17-alpine container.
Uses docker exec/psql over the local socket; never uses project credentials.
"""

import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

ROOT = Path(__file__).parents[2]
CONTAINER = os.getenv("FLOWPILOT_TEST_PG_CONTAINER")
pytestmark = pytest.mark.skipif(not CONTAINER, reason="Optional disposable PostgreSQL verification")
WORKFLOW = json.loads((ROOT / "n8n/flowpilot-followup-workflow.json").read_text())
NODES = {node["name"]: node for node in WORKFLOW["nodes"]}
APPROVAL = json.loads((ROOT / "n8n/flowpilot-approval-workflow.json").read_text())
SCHEDULE = next(n for n in APPROVAL["nodes"] if n["name"] == "Record Response Sent Timestamp")["parameters"]["query"]
CLAIM = NODES["Atomically Claim Due Follow-ups"]["parameters"]["query"].rstrip(";")


def command():
    return ["docker", "exec", "-i", CONTAINER, "psql", "-U", "postgres", "-d", "postgres", "-qAt", "-v", "ON_ERROR_STOP=1"]


def sql(schema, query, check=True):
    result = subprocess.run(command(), input=f'SET search_path TO "{schema}";\n' + query,
                            capture_output=True, text=True, timeout=20)
    if check:
        assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def db():
    schema = "fp7_" + uuid.uuid4().hex
    sql(schema, f'CREATE SCHEMA "{schema}";')
    for migration in sorted((ROOT / "postgres/init").glob("*.sql")):
        sql(schema, migration.read_text())
    yield schema
    sql(schema, f'DROP SCHEMA "{schema}" CASCADE;')


def seed(db, count=1):
    sql(db, f"""INSERT INTO leads
        (name,email,company,message,budget,category,priority,lead_score,short_summary,recommended_action,
         draft_status,initial_response_sent_at,followup_status,followup_due_at)
        SELECT 'Example','lead@example.com','Example','Test',100,'automation','high',80,'Test','Reply',
               'approved',NOW()-INTERVAL '73 hours','scheduled',NOW()-INTERVAL '1 hour'
        FROM generate_series(1,{count});""")


def claim(db):
    return json.loads(sql(db, f"WITH claimed AS ({CLAIM}) SELECT COALESCE(json_agg(claimed),'[]') FROM claimed;").stdout)


def test_migration_rerun_and_constraint(db):
    seed(db)
    before = sql(db, "SELECT row_to_json(leads) FROM leads;").stdout
    migration = (ROOT / "postgres/init/004_add_followups.sql").read_text()
    sql(db, migration)
    sql(db, migration)
    assert sql(db, "SELECT row_to_json(leads) FROM leads;").stdout == before
    result = sql(db, "UPDATE leads SET followup_status='invalid';", check=False)
    assert result.returncode != 0 and "leads_followup_status_check" in result.stderr


@pytest.mark.parametrize("state", ["sent", "failed", "sending", "cancelled", None])
def test_non_scheduled_states_are_not_claimed(db, state):
    seed(db)
    value = "NULL" if state is None else f"'{state}'"
    sql(db, f"UPDATE leads SET followup_status={value};")
    assert claim(db) == []


@pytest.mark.parametrize("change", [
    "draft_status='rejected'", "draft_status='pending_approval'",
    "initial_response_sent_at=NULL", "email=''", "email='   '", "email='invalid'",
    "email='a,b@example.com'", "email=E'a@example.com\\nBcc: b@example.com'",
    "followup_sent_at=NOW()",
    "followup_due_at=NOW()+INTERVAL '1 minute'",
])
def test_ineligible_leads_are_not_claimed(db, change):
    seed(db)
    sql(db, f"UPDATE leads SET {change};")
    assert claim(db) == []


def test_exact_72_hours_and_71h59m_without_waiting(db):
    seed(db, 2)
    query = f"""BEGIN;
        UPDATE leads SET initial_response_sent_at=NOW()-INTERVAL '72 hours',followup_due_at=NOW() WHERE id=1;
        UPDATE leads SET initial_response_sent_at=NOW()-INTERVAL '71 hours 59 minutes',followup_due_at=NOW()+INTERVAL '1 minute' WHERE id=2;
        WITH claimed AS ({CLAIM}) SELECT json_agg(claimed) FROM claimed;
        COMMIT;"""
    result = json.loads(sql(db, query).stdout)
    assert [row["lead_id"] for row in result] == ["1"]


def test_schedule_is_atomic_72_hours_and_single_use(db):
    seed(db, 2)
    sql(db, "UPDATE leads SET initial_response_sent_at=NULL,followup_status=NULL,followup_due_at=NULL;")
    sql(db, "PREPARE schedule(bigint) AS " + SCHEDULE + " EXECUTE schedule(1);")
    assert sql(db, "SELECT followup_status,EXTRACT(EPOCH FROM (followup_due_at-initial_response_sent_at)) FROM leads WHERE id=1;").stdout.strip() == "scheduled|259200.000000"
    before = sql(db, "SELECT row_to_json(leads) FROM leads WHERE id=1;").stdout
    sql(db, "PREPARE schedule(bigint) AS " + SCHEDULE + " EXECUTE schedule(1);")
    assert sql(db, "SELECT row_to_json(leads) FROM leads WHERE id=1;").stdout == before
    # Simulated initial SMTP failure: no timestamp query executed for row 2.
    assert sql(db, "SELECT initial_response_sent_at IS NULL AND followup_status IS NULL FROM leads WHERE id=2;").stdout.strip() == "t"


@pytest.mark.parametrize("change", ["draft_status='pending_approval'", "draft_status='rejected'", "email='bad'"])
def test_timestamp_query_cannot_schedule_ineligible_leads(db, change):
    seed(db)
    sql(db, f"UPDATE leads SET initial_response_sent_at=NULL,followup_status=NULL,followup_due_at=NULL,{change};")
    sql(db, "PREPARE schedule(bigint) AS " + SCHEDULE + " EXECUTE schedule(1);")
    assert sql(db, "SELECT followup_status IS NULL FROM leads;").stdout.strip() == "t"


def test_operator_cancellation_is_preserved_during_scheduling(db):
    seed(db)
    sql(db, "UPDATE leads SET initial_response_sent_at=NULL,followup_status='cancelled';")
    sql(db, "PREPARE schedule(bigint) AS " + SCHEDULE + " EXECUTE schedule(1);")
    assert sql(db, "SELECT followup_status FROM leads;").stdout.strip() == "cancelled"
    assert claim(db) == []


def test_multiple_rows_batch_limit_two_runs_and_zero_due(db):
    assert claim(db) == []
    seed(db, 25)
    first = claim(db)
    second = claim(db)
    assert len(first) == 20 and len(second) == 5
    assert {row["lead_id"] for row in first}.isdisjoint(row["lead_id"] for row in second)
    assert claim(db) == []


def test_concurrent_transactions_skip_claimed_rows(db):
    seed(db, 25)
    # Keep transaction A open after claiming. Reading its output is the barrier;
    # no timing assumptions or sleeps are needed before transaction B starts.
    process = subprocess.Popen(command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        process.stdin.write(f'SET search_path TO "{db}"; BEGIN;\n'
                            f"WITH claimed AS ({CLAIM}) SELECT jsonb_agg(claimed) FROM claimed;\n")
        process.stdin.flush()
        first = json.loads(process.stdout.readline())
        second = claim(db)
        assert len(first) == 20 and len(second) == 5
        assert {row["lead_id"] for row in first}.isdisjoint(row["lead_id"] for row in second)
        process.stdin.write("COMMIT;\n")
        process.stdin.flush()
        process.communicate(timeout=10)
        assert process.returncode == 0
        assert claim(db) == []
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


@pytest.mark.parametrize("node,status", [("Mark Follow-up Sent", "sent"), ("Mark Follow-up Failed", "failed")])
def test_send_result_updates_only_same_claim_without_retries(db, node, status):
    seed(db)
    row = claim(db)[0]
    before = sql(db, "SELECT draft_status,initial_response_sent_at FROM leads;").stdout
    query = NODES[node]["parameters"]["query"]
    result = sql(db, "PREPARE mark(bigint,timestamptz) AS " + query + " EXECUTE mark(1,'2000-01-01');")
    assert result.stdout.strip() == "f"
    result = sql(db, "PREPARE mark(bigint,timestamptz) AS " + query + f" EXECUTE mark(1,'{row['claim_time']}');")
    assert result.stdout.strip() == "t"
    assert sql(db, "SELECT followup_status FROM leads;").stdout.strip() == status
    assert sql(db, "SELECT followup_sent_at IS NULL FROM leads;").stdout.strip() == ("f" if status == "sent" else "t")
    assert sql(db, "SELECT draft_status,initial_response_sent_at FROM leads;").stdout == before
    assert claim(db) == []
