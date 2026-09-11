"""PostgreSQL 17 integration tests for Phase 8B2 reconciliation SQL.

Set FLOWPILOT_TEST_PG_CONTAINER to a fresh disposable postgres:17 container.
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
LEAD = json.loads((ROOT / "n8n/flowpilot-lead-workflow.json").read_text())
RECOVERY = json.loads((ROOT / "n8n/flowpilot-recovery-workflow.json").read_text())
NODES = {node["name"]: node for node in LEAD["nodes"]}
RECOVERY_NODES = {node["name"]: node for node in RECOVERY["nodes"]}
CLAIM = NODES["Atomically Claim Idempotency Key"]["parameters"]["query"].rstrip(";")
START = NODES["Mark Idempotency Business Started"]["parameters"]["query"].rstrip(";")
STORE = NODES["Record Idempotency Business Complete"]["parameters"]["query"].rstrip(";")
FINALIZE = NODES["Mark Idempotency Completed"]["parameters"]["query"].rstrip(";")
CLASSIFY_FAILURE = NODES["Mark Idempotency Failed"]["parameters"]["query"].rstrip(";")
RECOVERY_SWEEP = RECOVERY_NODES["Reconcile Stale Work"]["parameters"]["query"].rstrip(";")


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


def sql(schema: str, query: str, check: bool = True) -> subprocess.CompletedProcess[str]:
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


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@pytest.fixture
def db():
    schema = "fp8b2_" + uuid.uuid4().hex
    sql(schema, f'CREATE SCHEMA "{schema}";')
    for migration in sorted((ROOT / "postgres/init").glob("*.sql")):
        sql(schema, migration.read_text())
    yield schema
    sql(schema, f'DROP SCHEMA "{schema}" CASCADE;')


def claim(db: str, key: str, payload: str = '[["name","Example"]]') -> dict[str, str | None]:
    result = sql(
        db,
        "PREPARE fp_claim(text,text) AS "
        + CLAIM
        + f"; EXECUTE fp_claim({literal(key)},{literal(payload)});",
    ).stdout.strip()
    outcome, fingerprint, token, response = result.split("|", 3)
    return {
        "outcome": outcome,
        "fingerprint": fingerprint,
        "token": token,
        "response": response or None,
    }


def owner_arguments(key: str, row: dict[str, str | None]) -> str:
    return ",".join(
        [literal(key), literal(str(row["fingerprint"])), literal(str(row["token"]))]
    )


def owner_update(db: str, query: str, key: str, row: dict[str, str | None]) -> str:
    return sql(
        db,
        "PREPARE fp_owner(text,text,uuid) AS "
        + query
        + f"; EXECUTE fp_owner({owner_arguments(key, row)});",
    ).stdout.strip()


def store_response(
    db: str,
    key: str,
    row: dict[str, str | None],
    response: dict[str, object] | list[object] | str,
) -> str:
    payload = json.dumps(response)
    return sql(
        db,
        "PREPARE fp_store(text,text,uuid,jsonb) AS "
        + STORE
        + f"; EXECUTE fp_store({owner_arguments(key, row)},{literal(payload)});",
    ).stdout.strip()


def valid_response(priority: str = "high") -> dict[str, object]:
    return {
        "success": True,
        "route": priority,
        "message": f"{priority.title()}-priority lead received.",
        "analysis": {
            "category": "workflow automation",
            "priority": priority,
            "lead_score": 88,
            "short_summary": "Inventory automation requested.",
            "recommended_action": "Schedule a discovery call.",
        },
    }


def recover(db: str) -> list[int]:
    output = sql(db, RECOVERY_SWEEP).stdout.strip()
    return [int(value) for value in output.split("|")]


def test_migration_reruns_preserve_existing_rows_and_reject_invalid_states(db):
    row = claim(db, "preserved")
    before = sql(
        db,
        "SELECT row_to_json(flowpilot_idempotency) FROM flowpilot_idempotency;",
    ).stdout
    migration = (ROOT / "postgres/init/007_add_partial_work_reconciliation.sql").read_text()
    sql(db, migration)
    sql(db, migration)
    assert sql(
        db,
        "SELECT row_to_json(flowpilot_idempotency) FROM flowpilot_idempotency;",
    ).stdout == before
    assert row["outcome"] == "owner"
    invalid_status = sql(
        db,
        "UPDATE flowpilot_idempotency SET status='invalid' WHERE idempotency_key='preserved';",
        check=False,
    )
    invalid_stage = sql(
        db,
        "UPDATE flowpilot_idempotency SET workflow_stage='invalid' WHERE idempotency_key='preserved';",
        check=False,
    )
    assert invalid_status.returncode != 0
    assert invalid_stage.returncode != 0
    assert "violates check constraint" in invalid_status.stderr
    assert "violates check constraint" in invalid_stage.stderr
    constraints = sql(
        db,
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid='flowpilot_idempotency'::regclass "
        "AND conname IN ('flowpilot_idempotency_status_check',"
        "'flowpilot_idempotency_workflow_stage_check') ORDER BY conname;",
    ).stdout.strip().splitlines()
    assert constraints == [
        "flowpilot_idempotency_status_check",
        "flowpilot_idempotency_workflow_stage_check",
    ]


def test_new_claim_starts_processing_at_claimed_with_timestamp(db):
    row = claim(db, "new-claim")
    assert row["outcome"] == "owner"
    assert sql(
        db,
        "SELECT status,workflow_stage,stage_updated_at IS NOT NULL,response_payload IS NULL "
        "FROM flowpilot_idempotency WHERE idempotency_key='new-claim';",
    ).stdout.strip() == "processing|claimed|t|t"


def test_only_original_owner_can_cross_business_started_boundary(db):
    key = "start-owner"
    row = claim(db, key)
    wrong = dict(row, token="00000000-0000-4000-8000-000000000099")
    assert owner_update(db, START, key, wrong) == "f"
    assert sql(
        db,
        "SELECT workflow_stage FROM flowpilot_idempotency WHERE idempotency_key='start-owner';",
    ).stdout.strip() == "claimed"
    assert owner_update(db, START, key, row) == "t"
    assert owner_update(db, START, key, row) == "f"
    assert sql(
        db,
        "SELECT workflow_stage FROM flowpilot_idempotency WHERE idempotency_key='start-owner';",
    ).stdout.strip() == "business_started"


@pytest.mark.parametrize(
    ("cross_boundary", "expected"), [(False, "failed"), (True, "recovery_required")]
)
def test_known_failure_is_classified_by_durable_stage(db, cross_boundary, expected):
    key = f"failure-{expected}"
    row = claim(db, key)
    if cross_boundary:
        assert owner_update(db, START, key, row) == "t"
    result = owner_update(db, CLASSIFY_FAILURE, key, row)
    assert result.split("|") == ["t", expected]
    assert sql(
        db,
        f"SELECT status FROM flowpilot_idempotency WHERE idempotency_key={literal(key)};",
    ).stdout.strip() == expected


def test_business_complete_storage_and_finalization_are_separate_and_guarded(db):
    key = "safe-complete"
    row = claim(db, key)
    assert owner_update(db, START, key, row) == "t"
    assert store_response(db, key, row, valid_response()) == "t"
    assert sql(
        db,
        "SELECT status,workflow_stage,response_payload IS NOT NULL,completed_at IS NULL "
        "FROM flowpilot_idempotency WHERE idempotency_key='safe-complete';",
    ).stdout.strip() == "processing|business_complete|t|t"
    wrong = dict(row, token="00000000-0000-4000-8000-000000000099")
    assert owner_update(db, FINALIZE, key, wrong) == "f"
    assert owner_update(db, FINALIZE, key, row) == "t"
    assert sql(
        db,
        "SELECT status,workflow_stage,completed_at IS NOT NULL "
        "FROM flowpilot_idempotency WHERE idempotency_key='safe-complete';",
    ).stdout.strip() == "completed|business_complete|t"
    replay = claim(db, key)
    assert replay["outcome"] == "completed"
    assert json.loads(str(replay["response"])) == valid_response()


def test_finalization_requires_business_complete_and_same_fingerprint(db):
    key = "premature-finalize"
    row = claim(db, key)
    assert owner_update(db, FINALIZE, key, row) == "f"
    assert owner_update(db, START, key, row) == "t"
    assert owner_update(db, FINALIZE, key, row) == "f"
    wrong = dict(row, fingerprint="0" * 64)
    assert store_response(db, key, wrong, valid_response()) == "f"
    assert sql(
        db,
        "SELECT status,workflow_stage,response_payload IS NULL FROM flowpilot_idempotency;",
    ).stdout.strip() == "processing|business_started|t"


@pytest.mark.parametrize(
    "payload",
    [
        {"unsafe": "internal"},
        {**valid_response(), "lead_id": 42},
        {**valid_response(), "analysis": {**valid_response()["analysis"], "approval_token": "x"}},
        {**valid_response(), "route": "low"},
        "not-an-object",
    ],
)
def test_business_complete_rejects_malformed_or_unsafe_payload(db, payload):
    key = "unsafe-" + uuid.uuid4().hex
    row = claim(db, key)
    assert owner_update(db, START, key, row) == "t"
    assert store_response(db, key, row, payload) == "f"
    assert sql(
        db,
        f"SELECT status,workflow_stage,response_payload IS NULL FROM flowpilot_idempotency WHERE idempotency_key={literal(key)};",
    ).stdout.strip() == "processing|business_started|t"


def test_recovery_reconciles_stale_stages_and_preserves_fresh_and_terminal(db):
    rows = {}
    for key in (
        "stale-claimed",
        "fresh-claimed",
        "stale-started",
        "fresh-started",
        "stale-complete",
        "fresh-complete",
        "already-completed",
        "already-failed",
        "already-recovery",
    ):
        rows[key] = claim(db, key)
    for key in ("stale-started", "fresh-started", "stale-complete", "fresh-complete", "already-completed", "already-recovery"):
        assert owner_update(db, START, key, rows[key]) == "t"
    for key in ("stale-complete", "fresh-complete", "already-completed"):
        assert store_response(db, key, rows[key], valid_response()) == "t"
    assert owner_update(db, FINALIZE, "already-completed", rows["already-completed"]) == "t"
    assert owner_update(db, CLASSIFY_FAILURE, "already-failed", rows["already-failed"]).split("|")[1] == "failed"
    assert owner_update(db, CLASSIFY_FAILURE, "already-recovery", rows["already-recovery"]).split("|")[1] == "recovery_required"
    sql(
        db,
        "UPDATE flowpilot_idempotency SET stage_updated_at=NOW()-INTERVAL '61 minutes' "
        "WHERE idempotency_key IN ('stale-claimed','stale-started','stale-complete');"
        "UPDATE flowpilot_idempotency SET stage_updated_at=NOW()-INTERVAL '59 minutes 59 seconds' "
        "WHERE idempotency_key IN ('fresh-claimed','fresh-started','fresh-complete');"
        "INSERT INTO flowpilot_idempotency (idempotency_key,request_fingerprint,status,claim_token,created_at,updated_at) "
        f"VALUES ('legacy-stale','{'a' * 64}','processing',gen_random_uuid(),NOW()-INTERVAL '61 minutes',NOW()-INTERVAL '61 minutes');",
    )
    counts = recover(db)
    assert counts[2:] == [1, 1, 1, 0, 1]
    states = sql(
        db,
        "SELECT idempotency_key,status,COALESCE(workflow_stage,'null') "
        "FROM flowpilot_idempotency ORDER BY idempotency_key;",
    ).stdout.strip().splitlines()
    assert "stale-claimed|failed|claimed" in states
    assert "fresh-claimed|processing|claimed" in states
    assert "stale-started|recovery_required|business_started" in states
    assert "fresh-started|processing|business_started" in states
    assert "stale-complete|completed|business_complete" in states
    assert "fresh-complete|processing|business_complete" in states
    assert "already-completed|completed|business_complete" in states
    assert "legacy-stale|recovery_required|null" in states
    assert "already-failed|failed|claimed" in states
    assert "already-recovery|recovery_required|business_started" in states


def test_exact_sixty_minute_boundary_is_stale_without_sleep(db):
    row = claim(db, "exact-boundary")
    result = sql(
        db,
        "BEGIN;"
        "UPDATE flowpilot_idempotency SET stage_updated_at=NOW()-INTERVAL '60 minutes' "
        "WHERE idempotency_key='exact-boundary';"
        + RECOVERY_SWEEP
        + ";SELECT status FROM flowpilot_idempotency WHERE idempotency_key='exact-boundary';"
        "COMMIT;",
    )
    assert row["outcome"] == "owner"
    assert result.stdout.strip().splitlines()[-1] == "failed"


def test_stale_malformed_business_complete_is_quarantined_not_finalized(db):
    key = "malformed-complete"
    row = claim(db, key)
    assert owner_update(db, START, key, row) == "t"
    sql(
        db,
        "UPDATE flowpilot_idempotency SET workflow_stage='business_complete',"
        "response_payload='{" + '"unsafe":"internal"' + "}'::jsonb,"
        "stage_updated_at=NOW()-INTERVAL '61 minutes' "
        "WHERE idempotency_key='malformed-complete';",
    )
    counts = recover(db)
    assert counts[4:6] == [0, 1]
    assert sql(
        db,
        "SELECT status,completed_at IS NULL FROM flowpilot_idempotency "
        "WHERE idempotency_key='malformed-complete';",
    ).stdout.strip() == "recovery_required|t"


def test_recovery_required_cannot_be_reclaimed_and_conflict_still_wins(db):
    key = "manual-reconciliation"
    row = claim(db, key)
    assert owner_update(db, START, key, row) == "t"
    assert owner_update(db, CLASSIFY_FAILURE, key, row).split("|")[1] == "recovery_required"
    assert claim(db, key)["outcome"] == "recovery_required"
    assert claim(db, key, '[["name","Different"]]')["outcome"] == "conflict"
    assert sql(
        db,
        "SELECT status,claim_token::text FROM flowpilot_idempotency "
        "WHERE idempotency_key='manual-reconciliation';",
    ).stdout.strip() == f"recovery_required|{row['token']}"
