"""PostgreSQL execution tests for aggregate reconciliation observability."""

import os
from pathlib import Path
import subprocess
import uuid

import pytest

from app.services.reconciliation import RECONCILIATION_COUNTS_SQL


ROOT = Path(__file__).parents[2]
CONTAINER = os.getenv("FLOWPILOT_TEST_PG_CONTAINER")
pytestmark = pytest.mark.skipif(
    not CONTAINER, reason="Optional disposable PostgreSQL verification"
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
    schema = "fp_admin_" + uuid.uuid4().hex
    sql(schema, f'CREATE SCHEMA "{schema}";')
    for migration in sorted((ROOT / "postgres/init").glob("[0-9][0-9][0-9]_*.sql")):
        sql(schema, migration.read_text(encoding="utf-8"))
    yield schema
    sql(schema, f'DROP SCHEMA "{schema}" CASCADE;')


def test_reconciliation_aggregate_query_counts_states_without_mutation(db) -> None:
    sql(
        db,
        """INSERT INTO leads
        (name,email,company,message,budget,category,priority,lead_score,
         short_summary,recommended_action,draft_subject,draft_body,draft_status)
        SELECT 'Example','lead' || n || '@example.com','Example','Test',100,
               'automation','high',80,'Test','Reply','Subject','Body','approved'
        FROM generate_series(1,5) AS n;
        UPDATE leads SET initial_response_delivery_status='uncertain' WHERE id=1;
        UPDATE leads SET followup_status='uncertain' WHERE id=2;
        UPDATE leads SET initial_response_delivery_status='sending',
          initial_response_claim_token=gen_random_uuid(),
          initial_response_claimed_at=NOW()-INTERVAL '31 minutes' WHERE id=3;
        UPDATE leads SET followup_status='sending',
          followup_claimed_at=NOW()-INTERVAL '31 minutes' WHERE id=4;
        UPDATE leads SET initial_response_delivery_status='sending',
          initial_response_claim_token=gen_random_uuid(),
          initial_response_claimed_at=NOW()-INTERVAL '29 minutes' WHERE id=5;
        INSERT INTO flowpilot_idempotency
          (idempotency_key,request_fingerprint,status,claim_token,
           workflow_stage,stage_updated_at)
        VALUES
          ('admin-recovery',repeat('a',64),'recovery_required',gen_random_uuid(),
           'business_started',NOW()),
          ('admin-stale-claimed',repeat('b',64),'processing',gen_random_uuid(),
           'claimed',NOW()-INTERVAL '61 minutes'),
          ('admin-fresh-claimed',repeat('c',64),'processing',gen_random_uuid(),
           'claimed',NOW()-INTERVAL '59 minutes');""",
    )
    before = sql(
        db,
        "SELECT md5(string_agg(row_to_json(leads)::text,'|' ORDER BY id)) FROM leads;"
        "SELECT md5(string_agg(row_to_json(flowpilot_idempotency)::text,'|' "
        "ORDER BY idempotency_key)) FROM flowpilot_idempotency;",
    ).stdout

    assert sql(db, RECONCILIATION_COUNTS_SQL).stdout.strip() == "1|1|1|2|1"

    after = sql(
        db,
        "SELECT md5(string_agg(row_to_json(leads)::text,'|' ORDER BY id)) FROM leads;"
        "SELECT md5(string_agg(row_to_json(flowpilot_idempotency)::text,'|' "
        "ORDER BY idempotency_key)) FROM flowpilot_idempotency;",
    ).stdout
    assert after == before

