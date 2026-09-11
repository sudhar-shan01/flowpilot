"""Optional PostgreSQL 17 execution tests for the committed Phase 8A SQL."""

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
WORKFLOW = json.loads((ROOT / "n8n/flowpilot-lead-workflow.json").read_text())
NODES = {node["name"]: node for node in WORKFLOW["nodes"]}
CLAIM = NODES["Atomically Claim Idempotency Key"]["parameters"]["query"].rstrip(";")
START = NODES["Mark Idempotency Business Started"]["parameters"]["query"].rstrip(";")
STORE = NODES["Record Idempotency Business Complete"]["parameters"]["query"].rstrip(";")
COMPLETE = NODES["Mark Idempotency Completed"]["parameters"]["query"].rstrip(";")
FAIL = NODES["Mark Idempotency Failed"]["parameters"]["query"].rstrip(";")


def command() -> list[str]:
    return [
        "docker", "exec", "-i", CONTAINER, "psql", "-U", "postgres",
        "-d", "postgres", "-qAt", "-v", "ON_ERROR_STOP=1",
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
    schema = "fp8a_" + uuid.uuid4().hex
    sql(schema, f'CREATE SCHEMA "{schema}";')
    for migration in sorted((ROOT / "postgres/init").glob("*.sql")):
        sql(schema, migration.read_text())
    yield schema
    sql(schema, f'DROP SCHEMA "{schema}" CASCADE;')


def claim(db: str, key: str, payload: str) -> dict[str, object]:
    query = (
        "PREPARE fp_claim(text,text) AS " + CLAIM + ";"
        + f" EXECUTE fp_claim({literal(key)},{literal(payload)});"
    )
    output = sql(db, query).stdout.strip()
    assert output
    outcome, fingerprint, token, response = output.split("|", 3)
    return {
        "outcome": outcome,
        "request_fingerprint": fingerprint,
        "claim_token": token,
        "response_payload": json.loads(response) if response else None,
    }


def complete(db: str, key: str, row: dict[str, object], response: dict[str, object]):
    owner = ",".join(
        [
            literal(key),
            literal(str(row["request_fingerprint"])),
            literal(str(row["claim_token"])),
        ]
    )
    query = (
        "PREPARE fp_start(text,text,uuid) AS " + START + ";"
        + f" EXECUTE fp_start({owner});"
        + " PREPARE fp_store(text,text,uuid,jsonb) AS " + STORE + ";"
        + f" EXECUTE fp_store({owner},{literal(json.dumps(response))});"
        + " PREPARE fp_complete(text,text,uuid) AS " + COMPLETE + ";"
        + f" EXECUTE fp_complete({owner});"
    )
    outputs = sql(db, query).stdout.strip().splitlines()
    assert outputs[:2] == ["t", "t"]
    return outputs[-1]


def test_migration_reruns_without_data_loss(db):
    first = claim(db, "preserved-key", '[["name","Example"]]')
    before = sql(
        db,
        "SELECT row_to_json(flowpilot_idempotency) "
        "FROM flowpilot_idempotency WHERE idempotency_key='preserved-key';",
    ).stdout
    migration = (ROOT / "postgres/init/005_add_idempotency.sql").read_text()
    sql(db, migration)
    sql(db, migration)
    after = sql(
        db,
        "SELECT row_to_json(flowpilot_idempotency) "
        "FROM flowpilot_idempotency WHERE idempotency_key='preserved-key';",
    ).stdout
    assert first["outcome"] == "owner"
    assert after == before


def test_unique_constraint_rejects_duplicate_key(db):
    statement = (
        "INSERT INTO flowpilot_idempotency "
        "(idempotency_key,request_fingerprint,status) "
        "VALUES ('duplicate','" + "a" * 64 + "','processing');"
    )
    sql(db, statement)
    result = sql(db, statement, check=False)
    assert result.returncode != 0
    assert "flowpilot_idempotency_pkey" in result.stderr


def test_completed_replay_and_different_fingerprint_conflict(db):
    payload = '[["name","Example"]]'
    owner = claim(db, "completed-key", payload)
    response = {
        "success": True,
        "route": "high",
        "message": "Accepted",
        "analysis": {
            "category": "automation",
            "priority": "high",
            "lead_score": 90,
            "short_summary": "Example",
            "recommended_action": "Call",
        },
    }
    assert complete(db, "completed-key", owner, response) == "t"
    replay = claim(db, "completed-key", payload)
    assert replay["outcome"] == "completed"
    assert replay["response_payload"] == response
    conflict = claim(db, "completed-key", '[["name","Different"]]')
    assert conflict["outcome"] == "conflict"
    assert conflict["response_payload"] == response


def test_processing_key_with_different_fingerprint_conflicts_without_mutation(db):
    payload = '[["name","Original"]]'
    owner = claim(db, "processing-conflict", payload)
    before = sql(
        db,
        "SELECT request_fingerprint,status,claim_token,updated_at "
        "FROM flowpilot_idempotency WHERE idempotency_key='processing-conflict';",
    ).stdout
    conflict = claim(db, "processing-conflict", '[["name","Different"]]')
    after = sql(
        db,
        "SELECT request_fingerprint,status,claim_token,updated_at "
        "FROM flowpilot_idempotency WHERE idempotency_key='processing-conflict';",
    ).stdout
    assert owner["outcome"] == "owner"
    assert conflict["outcome"] == "conflict"
    assert after == before


def test_failed_request_is_not_blindly_replayed(db):
    owner = claim(db, "failed-key", '[["name","Example"]]')
    query = (
        "PREPARE fp_fail(text,text,uuid) AS " + FAIL + ";"
        + " EXECUTE fp_fail("
        + ",".join(
            [
                literal("failed-key"),
                literal(str(owner["request_fingerprint"])),
                literal(str(owner["claim_token"])),
            ]
        )
        + ");"
    )
    assert sql(db, query).stdout.strip().split("|")[0] == "t"
    assert claim(db, "failed-key", '[["name","Example"]]')["outcome"] == "failed"


def test_simultaneous_same_key_has_exactly_one_owner(db):
    payload = '[["name","Concurrent"]]'
    key = "same-concurrent-key"
    process_a = subprocess.Popen(
        command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    process_b = None
    try:
        process_a.stdin.write(
            f'SET search_path TO "{db}"; BEGIN;\n'
            + "PREPARE fp_claim_a(text,text) AS " + CLAIM + ";"
            + f" EXECUTE fp_claim_a({literal(key)},{literal(payload)});\n"
        )
        process_a.stdin.flush()
        first_parts = process_a.stdout.readline().strip().split("|", 3)
        first = {"outcome": first_parts[0]}

        process_b = subprocess.Popen(
            command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        process_b.stdin.write(
            f'SET search_path TO "{db}";\n'
            + "PREPARE fp_claim_b(text,text) AS " + CLAIM + ";"
            + f" EXECUTE fp_claim_b({literal(key)},{literal(payload)});\n"
        )
        process_b.stdin.close()
        process_a.stdin.write("COMMIT;\n")
        process_a.stdin.flush()
        process_a.stdin.close()
        second_output = process_b.stdout.read().strip()
        second_error = process_b.stderr.read()
        process_b.wait(timeout=10)
        process_a.wait(timeout=10)
        assert process_a.returncode == 0
        assert process_b.returncode == 0, second_error
        second = {"outcome": second_output.split("|", 3)[0]}
        assert sorted([first["outcome"], second["outcome"]]) == [
            "in_progress", "owner"
        ]
        assert sql(
            db,
            "SELECT COUNT(*) FROM flowpilot_idempotency "
            "WHERE idempotency_key='same-concurrent-key';",
        ).stdout.strip() == "1"
    finally:
        for process in (process_a, process_b):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()


def test_concurrent_different_keys_can_both_succeed(db):
    barrier = threading.Barrier(2)

    def run(number: int):
        barrier.wait()
        return claim(db, f"different-key-{number}", f'[["budget",{number}]]')

    with ThreadPoolExecutor(max_workers=2) as executor:
        rows = list(executor.map(run, [1, 2]))
    assert [row["outcome"] for row in rows] == ["owner", "owner"]
    assert sql(
        db,
        "SELECT COUNT(*) FROM flowpilot_idempotency "
        "WHERE idempotency_key LIKE 'different-key-%';",
    ).stdout.strip() == "2"
