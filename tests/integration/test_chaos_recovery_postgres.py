"""PostgreSQL chaos certification for a worker crash during initial email.

Set FLOWPILOT_TEST_PG_CONTAINER to a fresh disposable PostgreSQL 17 container.
The test executes the production claim and recovery SQL and never calls SMTP.
"""

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


def workflow_query(path: str, node_name: str) -> str:
    workflow = json.loads((ROOT / path).read_text(encoding="utf-8"))
    return next(
        node["parameters"]["query"].rstrip(";")
        for node in workflow["nodes"]
        if node["name"] == node_name
    )


INITIAL_CLAIM = workflow_query(
    "n8n/flowpilot-approval-workflow.json", "Atomically Claim Initial Response"
)
RECOVERY_SWEEP = workflow_query(
    "n8n/flowpilot-recovery-workflow.json", "Reconcile Stale Work"
)


def command() -> list[str]:
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


def sql(schema: str, query: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command(),
        input=f'SET search_path TO "{schema}";\n{query}',
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def db():
    schema = "fp_chaos_" + uuid.uuid4().hex
    sql(schema, f'CREATE SCHEMA "{schema}";')
    for migration in sorted((ROOT / "postgres/init").glob("[0-9][0-9][0-9]_*.sql")):
        sql(schema, migration.read_text(encoding="utf-8"))
    yield schema
    sql(schema, f'DROP SCHEMA "{schema}" CASCADE;')


def test_stale_initial_send_is_quarantined_once_without_retry_or_smtp(db):
    sql(
        db,
        """INSERT INTO leads
        (name,email,company,message,budget,category,priority,lead_score,
         short_summary,recommended_action,draft_subject,draft_body,draft_status)
        VALUES
        ('Chaos Example','chaos@example.com','Example','Test',100,
         'automation','high',80,'Test','Reply','Subject','Body','approved');""",
    )

    claimed = sql(
        db,
        "PREPARE initial_claim(bigint) AS "
        + INITIAL_CLAIM
        + "; EXECUTE initial_claim(1);",
    ).stdout.strip().split("|")
    assert claimed[0] == "owner"
    assert claimed[5]
    assert sql(
        db,
        "SELECT initial_response_delivery_status,"
        "initial_response_claim_token IS NOT NULL,"
        "initial_response_sent_at IS NULL FROM leads WHERE id=1;",
    ).stdout.strip() == "sending|t|t"

    # Simulate the worker disappearing after its durable claim but before it can
    # record any terminal SMTP outcome. No SMTP client is invoked by this test.
    sql(
        db,
        "UPDATE leads SET initial_response_claimed_at="
        "NOW()-INTERVAL '31 minutes' WHERE id=1;",
    )
    first_recovery = sql(db, RECOVERY_SWEEP).stdout.strip().split("|")
    assert first_recovery[0] == "1"

    quarantined = sql(
        db,
        "SELECT initial_response_delivery_status,"
        "initial_response_claim_token::text,"
        "initial_response_sent_at IS NULL,"
        "followup_status IS NULL FROM leads WHERE id=1;",
    ).stdout.strip()
    assert quarantined == f"uncertain|{claimed[5]}|t|t"

    # Uncertain is terminal for automatic claiming; the original work is not
    # reset, made retryable, or sent by running recovery again.
    blocked = sql(
        db,
        "PREPARE retry_claim(bigint) AS "
        + INITIAL_CLAIM
        + "; EXECUTE retry_claim(1);",
    ).stdout.strip().split("|")
    assert blocked[0] == "blocked"
    second_recovery = sql(db, RECOVERY_SWEEP).stdout.strip().split("|")
    assert second_recovery[0] == "0"
    assert sql(
        db,
        "SELECT initial_response_delivery_status,"
        "initial_response_claim_token::text,"
        "initial_response_sent_at IS NULL,"
        "followup_status IS NULL FROM leads WHERE id=1;",
    ).stdout.strip() == quarantined
