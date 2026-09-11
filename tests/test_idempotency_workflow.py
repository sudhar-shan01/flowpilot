"""Static and Code-node regression tests for the Phase 8A idempotency boundary."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.test_human_approval_workflow import first_target, reachable

ROOT = Path(__file__).parents[1]
WORKFLOW_PATH = ROOT / "n8n" / "flowpilot-lead-workflow.json"
MIGRATION_PATH = ROOT / "postgres" / "init" / "005_add_idempotency.sql"
WORKFLOW = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
NODES = {node["name"]: node for node in WORKFLOW["nodes"]}
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")
CLAIM = NODES["Atomically Claim Idempotency Key"]["parameters"]["query"]


def run_code(name: str, data: dict[str, object]) -> dict[str, object]:
    node = shutil.which("node")
    assert node, "Install Node.js to run the n8n Code-node regression tests"
    program = r"""
const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const result = vm.runInNewContext(
  '(function(){' + input.code + '\n})()',
  {$json: input.data},
  {timeout: 1000},
);
process.stdout.write(JSON.stringify(result[0].json));
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"code": NODES[name]["parameters"]["jsCode"], "data": data}),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(result.stdout)


def envelope(key=..., **changes):
    body = {
        "name": "Example Lead",
        "email": "lead@example.com",
        "company": "Example Co",
        "message": "Please automate our inventory workflow.",
        "budget": 5000,
    }
    body.update(changes)
    headers = {} if key is ... else {"idempotency-key": key}
    return {"headers": headers, "body": body}


def reachable_from_output(source: str, output_index: int) -> set[str]:
    targets = WORKFLOW["connections"][source]["main"][output_index]
    result: set[str] = set()
    for target in targets:
        result.add(target["node"])
        result.update(reachable(WORKFLOW, target["node"]))
    return result


def test_migration_is_additive_repeatable_and_minimal() -> None:
    assert MIGRATION.startswith("BEGIN;")
    assert MIGRATION.rstrip().endswith("COMMIT;")
    assert "CREATE TABLE IF NOT EXISTS flowpilot_idempotency" in MIGRATION
    assert "idempotency_key VARCHAR(128) PRIMARY KEY" in MIGRATION
    assert "request_fingerprint CHAR(64) NOT NULL" in MIGRATION
    assert "claim_token UUID NOT NULL DEFAULT gen_random_uuid()" in MIGRATION
    assert "response_payload JSONB" in MIGRATION
    assert "('processing', 'completed', 'failed')" in MIGRATION
    for destructive in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER TABLE leads"):
        assert destructive not in MIGRATION


def test_gate_is_before_every_business_side_effect() -> None:
    assert first_target(WORKFLOW, "Lead Intake Webhook") == "Prepare Idempotency Context"
    assert first_target(WORKFLOW, "Prepare Idempotency Context") == "Route Idempotency Requirement"
    assert first_target(WORKFLOW, "Route Idempotency Requirement", 0) == (
        "Atomically Claim Idempotency Key"
    )
    side_effects = {
        "Analyze Lead with FlowPilot",
        "Persist Lead in PostgreSQL",
        "Append Lead to Google Sheets",
        "Search HubSpot Contact by Email",
        "Update HubSpot Contact",
        "Create HubSpot Contact",
        "Generate Lead Response Draft",
        "Create Approval Token",
        "Send Internal Notification",
    }
    assert not side_effects & reachable_from_output("Route Idempotency Claim", 1)
    assert not side_effects & reachable_from_output("Route Idempotency Claim", 2)
    assert side_effects <= reachable_from_output("Route Idempotency Claim", 0)


def test_atomic_claim_uses_unique_key_without_select_then_insert() -> None:
    assert "INSERT INTO flowpilot_idempotency" in CLAIM
    assert "ON CONFLICT (idempotency_key) DO UPDATE" in CLAIM
    assert "gen_random_uuid()" in CLAIM
    assert "claim_token = (SELECT attempted_claim_token FROM candidate)" in CLAIM
    assert "encode(sha256(convert_to($2::text, 'UTF8')), 'hex')" in CLAIM
    assert CLAIM.index("INSERT INTO") < CLAIM.index("CASE")
    assert NODES["Atomically Claim Idempotency Key"]["parameters"]["options"][
        "queryReplacement"
    ] == "={{ [$json.idempotency_key, $json.canonical_payload] }}"


def test_fingerprint_normalizes_all_business_fields_deterministically() -> None:
    first = run_code(
        "Prepare Idempotency Context",
        envelope("invoice:2026/09+retry=1", name="  Example Lead  ", budget="5000"),
    )
    second = run_code(
        "Prepare Idempotency Context",
        envelope("invoice:2026/09+retry=1", name="Example Lead", budget=5000),
    )
    assert first["idempotency_mode"] == "valid"
    assert first["canonical_payload"] == second["canonical_payload"]
    assert first["body"] == second["body"]
    canonical = json.loads(first["canonical_payload"])
    assert [field for field, _ in canonical] == [
        "name", "email", "company", "message", "budget"
    ]
    field_names = {field.lower() for field, _ in canonical}
    for forbidden in ("timestamp", "uuid", "credential", "workflow", "idempotency"):
        assert forbidden not in field_names


@pytest.mark.parametrize(
    "key",
    ["", " ", "\t", "x" * 129, "ok\r\nX-Injected: yes", "has space", "_starts-wrong"],
)
def test_invalid_keys_are_rejected_before_postgres(key: str) -> None:
    result = run_code("Prepare Idempotency Context", envelope(key))
    assert result["idempotency_mode"] == "invalid"
    assert result["statusCode"] == 400
    assert result["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"
    assert result["idempotency_key"] is None


@pytest.mark.parametrize(
    "key",
    ["a", "ABC-123_xyz", "tenant:invoice/2026.09~retry+one=1", "x" * 128],
)
def test_conservative_allowed_key_characters(key: str) -> None:
    result = run_code("Prepare Idempotency Context", envelope(key))
    assert result["idempotency_mode"] == "valid"
    assert result["idempotency_key"] == key


def test_missing_key_preserves_backward_compatible_path() -> None:
    result = run_code("Prepare Idempotency Context", envelope())
    assert result["idempotency_mode"] == "absent"
    assert first_target(WORKFLOW, "Route Idempotency Requirement", 1) == "Prepare Lead"
    assert "Atomically Claim Idempotency Key" not in reachable_from_output(
        "Route Idempotency Requirement", 1
    )


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        ("in_progress", "REQUEST_IN_PROGRESS"),
        ("conflict", "IDEMPOTENCY_CONFLICT"),
        ("failed", "REQUEST_FAILED"),
        ("recovery_required", "RECONCILIATION_REQUIRED"),
    ],
)
def test_duplicate_and_conflict_outcomes_are_sanitized(outcome: str, code: str) -> None:
    result = run_code(
        "Sanitize Idempotency Claim",
        {
            "outcome": outcome,
            "request_fingerprint": "a" * 64,
            "claim_token": "not-public",
            "response_payload": {"secret": "never expose"},
        },
    )
    assert result["idempotency_action"] == "error"
    assert result["statusCode"] == 409
    assert result["error"]["code"] == code
    assert "secret" not in json.dumps(result)


def test_completed_replay_rebuilds_only_public_contract() -> None:
    payload = {
        "success": True,
        "route": "high",
        "message": "High-priority lead received.",
        "analysis": {
            "category": "workflow automation",
            "priority": "high",
            "lead_score": 88,
            "short_summary": "Inventory automation requested.",
            "recommended_action": "Schedule a discovery call.",
            "approval_token": "forbidden",
        },
        "lead_id": 42,
        "draft": "forbidden",
        "provider": {"raw": "forbidden"},
    }
    result = run_code(
        "Sanitize Idempotency Claim",
        {
            "outcome": "completed",
            "request_fingerprint": "a" * 64,
            "claim_token": "forbidden",
            "response_payload": payload,
        },
    )
    assert result["idempotency_action"] == "replay"
    assert result["public_response"] == {
        "success": True,
        "route": "high",
        "message": "High-priority lead received.",
        "analysis": {
            "category": "workflow automation",
            "priority": "high",
            "lead_score": 88,
            "short_summary": "Inventory automation requested.",
            "recommended_action": "Schedule a discovery call.",
        },
    }
    for forbidden in ("approval_token", "lead_id", "draft", "provider", "claim_token"):
        assert forbidden not in json.dumps(result)


def test_only_owner_and_unkeyed_paths_can_enter_business_workflow() -> None:
    assert first_target(WORKFLOW, "Route Idempotency Claim", 0) == "Prepare Lead"
    assert first_target(WORKFLOW, "Route Idempotency Claim", 1) == (
        "Respond Idempotency Replay"
    )
    assert first_target(WORKFLOW, "Route Idempotency Claim", 2) == (
        "Respond Idempotency Error"
    )
    assert "Prepare Lead" not in reachable_from_output("Route Idempotency Claim", 1)
    assert "Prepare Lead" not in reachable_from_output("Route Idempotency Claim", 2)


def test_completed_and_failed_updates_require_original_claim() -> None:
    started = NODES["Mark Idempotency Business Started"]["parameters"]["query"]
    stored = NODES["Record Idempotency Business Complete"]["parameters"]["query"]
    completed = NODES["Mark Idempotency Completed"]["parameters"]["query"]
    failed = NODES["Mark Idempotency Failed"]["parameters"]["query"]
    for query in (started, stored, completed, failed):
        assert "idempotency_key = $1" in query
        assert "request_fingerprint = $2" in query
        assert "claim_token = $3::uuid" in query
        assert "status = 'processing'" in query
    assert "workflow_stage = 'claimed'" in started
    assert "workflow_stage = 'business_started'" in started
    assert "response_payload = incoming.response_payload" in stored
    assert "workflow_stage = 'business_complete'" in stored
    assert "status = 'completed'" not in stored
    assert "response_payload" not in completed.split("WHERE", 1)[0]
    assert "workflow_stage = 'business_complete'" in completed
    assert "completed_at = NOW()" in completed
    assert "WHEN 'claimed' THEN 'failed'" in failed
    assert "WHEN 'business_started' THEN 'recovery_required'" in failed
    assert "response_payload" not in failed


def test_all_existing_error_paths_preserve_failed_record_for_keyed_requests() -> None:
    incoming = []
    for source, outputs in WORKFLOW["connections"].items():
        for output in outputs.get("main", []):
            for target in output:
                if target["node"] == "Respond Error":
                    incoming.append(source)
    assert set(incoming) == {"Fail Only Keyed Request", "Restore Sanitized Failure"}
    assert first_target(WORKFLOW, "Prepare Idempotency Failure") == "Fail Only Keyed Request"
    assert first_target(WORKFLOW, "Fail Only Keyed Request", 0) == "Mark Idempotency Failed"
    assert first_target(WORKFLOW, "Fail Only Keyed Request", 1) == "Respond Error"


def test_key_never_flows_to_external_business_nodes() -> None:
    for name in (
        "Append Lead to Google Sheets",
        "Search HubSpot Contact by Email",
        "Update HubSpot Contact",
        "Create HubSpot Contact",
        "Generate Lead Response Draft",
        "Prepare Internal Notification",
        "Send Internal Notification",
    ):
        serialized = json.dumps(NODES[name]["parameters"]).lower()
        assert "idempotency" not in serialized
    assert "console." not in NODES["Prepare Idempotency Context"]["parameters"]["jsCode"]


def test_same_email_with_different_keys_remains_independent() -> None:
    first = run_code("Prepare Idempotency Context", envelope("lead-attempt-1"))
    second = run_code("Prepare Idempotency Context", envelope("lead-attempt-2"))
    assert first["canonical_payload"] == second["canonical_payload"]
    assert first["idempotency_key"] != second["idempotency_key"]
    assert "email" not in MIGRATION.lower().split("flowpilot_idempotency", 1)[1]


def test_no_retry_or_recovery_mechanism_was_added() -> None:
    idempotency_nodes = [
        node for node in WORKFLOW["nodes"] if "Idempotency" in node["name"]
    ]
    assert idempotency_nodes
    assert all(not node.get("retryOnFail", False) for node in idempotency_nodes)
    serialized = json.dumps(idempotency_nodes).lower()
    for forbidden in ("poll", "backoff", "retry_count", "stale"):
        assert forbidden not in serialized


def test_export_has_reference_metadata_only_and_no_secrets() -> None:
    for name in (
        "Atomically Claim Idempotency Key",
        "Mark Idempotency Business Started",
        "Record Idempotency Business Complete",
        "Mark Idempotency Completed",
        "Mark Idempotency Failed",
    ):
        reference = NODES[name]["credentials"]["postgres"]
        assert set(reference) == {"id", "name"}
        assert reference["name"] == "FlowPilot PostgreSQL"
    text = WORKFLOW_PATH.read_text(encoding="utf-8").lower()
    for secret in ("postgres_password", "smtp_password", "oauth_token", "api_key"):
        assert secret not in text
