"""Static regression tests for Phase 8B2 partial-work reconciliation."""

import json
from pathlib import Path

from tests.test_human_approval_workflow import first_target, reachable


ROOT = Path(__file__).parents[1]
LEAD_PATH = ROOT / "n8n/flowpilot-lead-workflow.json"
RECOVERY_PATH = ROOT / "n8n/flowpilot-recovery-workflow.json"
MIGRATION_PATH = ROOT / "postgres/init/007_add_partial_work_reconciliation.sql"
LEAD = json.loads(LEAD_PATH.read_text(encoding="utf-8"))
RECOVERY = json.loads(RECOVERY_PATH.read_text(encoding="utf-8"))
NODES = {node["name"]: node for node in LEAD["nodes"]}
RECOVERY_NODES = {node["name"]: node for node in RECOVERY["nodes"]}
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")


BUSINESS_SIDE_EFFECTS = {
    "Persist Lead in PostgreSQL",
    "Append Lead to Google Sheets",
    "Search HubSpot Contact by Email",
    "Update HubSpot Contact",
    "Create HubSpot Contact",
    "Generate Lead Response Draft",
    "Persist Draft in PostgreSQL",
    "Create Approval Token",
    "Send Internal Notification",
}


def reachable_from_output(workflow: dict, source: str, output: int) -> set[str]:
    found: set[str] = set()
    for target in workflow["connections"][source]["main"][output]:
        found.add(target["node"])
        found.update(reachable(workflow, target["node"]))
    return found


def test_migration_is_additive_transactional_and_rerunnable() -> None:
    assert MIGRATION.startswith("BEGIN;")
    assert MIGRATION.rstrip().endswith("COMMIT;")
    assert "ADD COLUMN IF NOT EXISTS workflow_stage VARCHAR(17)" in MIGRATION
    assert "ADD COLUMN IF NOT EXISTS stage_updated_at TIMESTAMPTZ" in MIGRATION
    assert "ALTER COLUMN status TYPE VARCHAR(17)" in MIGRATION
    assert "'recovery_required'" in MIGRATION
    assert "'claimed', 'business_started', 'business_complete'" in MIGRATION
    for destructive in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER TABLE leads"):
        assert destructive not in MIGRATION


def test_migration_preserves_legacy_null_stages_and_constrains_new_state() -> None:
    assert "workflow_stage IS NULL" in MIGRATION
    assert "workflow_stage IS NULL OR stage_updated_at IS NOT NULL" in MIGRATION
    assert "status = 'processing'" in MIGRATION
    assert "status = 'completed'" in MIGRATION
    assert "status = 'failed'" in MIGRATION
    assert "status = 'recovery_required'" in MIGRATION
    assert "flowpilot_idempotency_processing_stage_index" in MIGRATION


def test_new_atomic_claim_initializes_claimed_stage() -> None:
    query = NODES["Atomically Claim Idempotency Key"]["parameters"]["query"]
    assert "workflow_stage" in query
    assert "stage_updated_at" in query
    assert "'processing'" in query
    assert "'claimed'" in query
    assert "NOW()" in query
    assert "ON CONFLICT (idempotency_key) DO UPDATE" in query
    assert "gen_random_uuid()" in query


def test_unkeyed_requests_keep_the_existing_direct_path() -> None:
    assert first_target(LEAD, "Route Idempotency Requirement", 1) == "Prepare Lead"
    assert first_target(LEAD, "Start Business Only If Keyed", 1) == (
        "Persist Lead in PostgreSQL"
    )
    switch = NODES["Start Business Only If Keyed"]
    assert switch["parameters"]["rules"]["values"][1]["outputKey"] == "unkeyed"


def test_keyed_business_started_gate_is_before_first_durable_side_effect() -> None:
    assert first_target(LEAD, "Persist Only Valid Analysis", 0) == (
        "Start Business Only If Keyed"
    )
    assert first_target(LEAD, "Start Business Only If Keyed", 0) == (
        "Mark Idempotency Business Started"
    )
    assert first_target(LEAD, "Route Business Started Result", 0) == (
        "Persist Lead in PostgreSQL"
    )
    assert "Persist Lead in PostgreSQL" not in reachable_from_output(
        LEAD, "Route Business Started Result", 1
    )


def test_business_started_is_one_guarded_owner_update() -> None:
    node = NODES["Mark Idempotency Business Started"]
    query = node["parameters"]["query"]
    assert query.count("UPDATE flowpilot_idempotency") == 1
    assert "workflow_stage = 'business_started'" in query
    assert "workflow_stage = 'claimed'" in query
    assert "status = 'processing'" in query
    assert "idempotency_key = $1" in query
    assert "request_fingerprint = $2" in query
    assert "claim_token = $3::uuid" in query
    assert node["onError"] == "continueRegularOutput"


def test_failed_business_started_gate_cannot_reach_side_effects() -> None:
    failed_path = reachable_from_output(LEAD, "Route Business Started Result", 1)
    assert not BUSINESS_SIDE_EFFECTS & failed_path
    assert first_target(LEAD, "Route Business Started Result", 1) == (
        "Respond Idempotency Error"
    )
    sanitizer = NODES["Sanitize Business Started"]["parameters"]["jsCode"]
    assert "IDEMPOTENCY_STATE_ERROR" in sanitizer
    assert "result.error" in sanitizer


def test_known_failures_are_classified_by_current_stage() -> None:
    query = NODES["Mark Idempotency Failed"]["parameters"]["query"]
    assert "WHEN 'claimed' THEN 'failed'" in query
    assert "WHEN 'business_started' THEN 'recovery_required'" in query
    assert "workflow_stage IN ('claimed', 'business_started')" in query
    assert "claim_token = $3::uuid" in query
    assert "status = 'processing'" in query
    assert "response_payload" not in query


def test_recovery_required_duplicate_is_blocked_and_conflict_has_precedence() -> None:
    claim = NODES["Atomically Claim Idempotency Key"]["parameters"]["query"]
    assert claim.index("request_fingerprint <> candidate.request_fingerprint") < claim.index(
        "claimed.status = 'recovery_required'"
    )
    sanitizer = NODES["Sanitize Idempotency Claim"]["parameters"]["jsCode"]
    assert "RECONCILIATION_REQUIRED" in sanitizer
    assert "IDEMPOTENCY_CONFLICT" in sanitizer
    assert not BUSINESS_SIDE_EFFECTS & reachable_from_output(
        LEAD, "Route Idempotency Claim", 2
    )


def test_public_response_is_stored_only_at_business_complete() -> None:
    store = NODES["Record Idempotency Business Complete"]["parameters"]["query"]
    assert "workflow_stage = 'business_complete'" in store
    assert "response_payload = incoming.response_payload" in store
    assert "workflow_stage = 'business_started'" in store
    assert "status = 'completed'" not in store
    assert "COUNT(*) FROM jsonb_object_keys(incoming.response_payload)" in store
    assert "COUNT(*) FROM jsonb_object_keys(incoming.response_payload->'analysis')" in store
    assert "['success', 'route', 'message', 'analysis']" in store
    assert "['category', 'priority', 'lead_score', 'short_summary', 'recommended_action']" in store
    writers = []
    for item in LEAD["nodes"]:
        query = item.get("parameters", {}).get("query", "")
        if "SET workflow_stage = 'business_complete'" in query:
            writers.append(item["name"])
    assert writers == ["Record Idempotency Business Complete"]


def test_final_completion_is_separate_and_requires_same_owner_and_stage() -> None:
    assert first_target(LEAD, "Complete Only Keyed Request", 0) == (
        "Record Idempotency Business Complete"
    )
    assert first_target(LEAD, "Route Business Complete Result", 0) == (
        "Mark Idempotency Completed"
    )
    query = NODES["Mark Idempotency Completed"]["parameters"]["query"]
    assert "status = 'completed'" in query
    assert "workflow_stage = 'business_complete'" in query
    assert "idempotency_key = $1" in query
    assert "request_fingerprint = $2" in query
    assert "claim_token = $3::uuid" in query
    assert "response_payload =" not in query


def test_failed_final_completion_does_not_reenter_business_work() -> None:
    failure_path = reachable_from_output(LEAD, "Route Completion Result", 1)
    assert not BUSINESS_SIDE_EFFECTS & failure_path
    assert first_target(LEAD, "Route Completion Result", 1) == (
        "Respond Idempotency Error"
    )
    assert not NODES["Mark Idempotency Completed"].get("retryOnFail", False)


def test_stale_reconciliation_has_explicit_sixty_minute_stage_rules() -> None:
    query = RECOVERY_NODES["Reconcile Stale Work"]["parameters"]["query"]
    assert query.count("INTERVAL '60 minutes'") == 5
    assert "workflow_stage = 'claimed'" in query
    assert "SET status = 'failed'" in query
    assert "workflow_stage = 'business_started'" in query
    assert query.count("SET status = 'recovery_required'") == 3
    assert "workflow_stage = 'business_complete'" in query
    assert "SET status = 'completed'" in query
    assert "workflow_stage IS NULL" in query
    assert "COALESCE(stage_updated_at, updated_at, created_at)" in query


def test_recovery_finalizes_only_strictly_valid_public_payloads() -> None:
    query = RECOVERY_NODES["Reconcile Stale Work"]["parameters"]["query"]
    assert "safe_business_complete" in query
    assert "unsafe_business_complete" in query
    assert "COUNT(*) FROM jsonb_object_keys(response_payload)" in query
    assert "COUNT(*) FROM jsonb_object_keys(response_payload->'analysis')" in query
    assert "AND NOT (CASE" in query
    assert "unsafe_recovery_required" in query


def test_email_recovery_threshold_and_states_are_unchanged() -> None:
    query = RECOVERY_NODES["Reconcile Stale Work"]["parameters"]["query"]
    assert query.count("INTERVAL '30 minutes'") == 2
    assert "initial_response_delivery_status = 'uncertain'" in query
    assert "initial_response_delivery_status = 'sending'" in query
    assert "followup_status = 'uncertain'" in query
    assert "followup_status = 'sending'" in query


def test_recovery_workflow_cannot_replay_any_side_effect() -> None:
    assert {node["type"] for node in RECOVERY["nodes"]} == {
        "n8n-nodes-base.scheduleTrigger",
        "n8n-nodes-base.postgres",
    }
    assert len(list((ROOT / "n8n").glob("*.json"))) == 4
    serialized = json.dumps(RECOVERY).lower()
    for forbidden in ("httpRequest", "emailSend", "googleSheets", "hubspot", "openai"):
        assert forbidden.lower() not in serialized


def test_no_automatic_reset_retry_or_reclaim_is_added() -> None:
    for workflow in (LEAD, RECOVERY):
        assert all(not node.get("retryOnFail", False) for node in workflow["nodes"])
    serialized = json.dumps([LEAD, RECOVERY]).lower()
    for forbidden in (
        "recovery_required' then 'processing",
        "recovery_required' then 'claimed",
        "uncertain' then 'sending",
        "uncertain' then 'scheduled",
        "retry_count",
        "exponential",
    ):
        assert forbidden not in serialized


def test_phase6_get_and_phase8b1_delivery_states_remain_protected() -> None:
    approval = json.loads(
        (ROOT / "n8n/flowpilot-approval-workflow.json").read_text(encoding="utf-8")
    )
    approval_nodes = {node["name"]: node for node in approval["nodes"]}
    get_path = {"Approval Review Webhook", *reachable(approval, "Approval Review Webhook")}
    for name in get_path:
        assert approval_nodes[name]["type"] not in {
            "n8n-nodes-base.postgres",
            "n8n-nodes-base.emailSend",
            "n8n-nodes-base.httpRequest",
        }
    migration6 = (ROOT / "postgres/init/006_add_email_recovery.sql").read_text(
        encoding="utf-8"
    )
    assert "('sending', 'sent', 'failed', 'uncertain')" in migration6
    assert "('scheduled', 'sending', 'sent', 'failed', 'cancelled', 'uncertain')" in migration6


def test_exports_have_credential_references_only_and_no_secrets() -> None:
    for workflow in (LEAD, RECOVERY):
        for item in workflow["nodes"]:
            for reference in item.get("credentials", {}).values():
                assert set(reference) == {"id", "name"}
    text = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (LEAD_PATH, RECOVERY_PATH, MIGRATION_PATH)
    )
    for secret in (
        "postgres_password",
        "smtp_password",
        "oauth_token",
        "api_key",
        "client_secret",
    ):
        assert secret not in text
