"""Static contracts for Phase 8C reliability observability."""

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION_PATH = ROOT / "postgres/init/008_add_reliability_observability.sql"
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")
RECOVERY_PATH = ROOT / "n8n/flowpilot-recovery-workflow.json"
RECOVERY = json.loads(RECOVERY_PATH.read_text(encoding="utf-8"))
RECOVERY_NODES = {node["name"]: node for node in RECOVERY["nodes"]}
QUERY = RECOVERY_NODES["Reconcile Stale Work"]["parameters"]["query"]


def test_migration_is_transactional_additive_and_rerunnable() -> None:
    assert MIGRATION.startswith("BEGIN;")
    assert MIGRATION.rstrip().endswith("COMMIT;")
    assert "CREATE TABLE IF NOT EXISTS flowpilot_reliability_events" in MIGRATION
    assert "CREATE TABLE IF NOT EXISTS flowpilot_recovery_runs" in MIGRATION
    assert "CREATE OR REPLACE FUNCTION flowpilot_audit_lead_change()" in MIGRATION
    assert "CREATE OR REPLACE FUNCTION flowpilot_audit_idempotency_change()" in MIGRATION
    assert "CREATE OR REPLACE VIEW flowpilot_reconciliation_queue" in MIGRATION
    assert "CREATE OR REPLACE VIEW flowpilot_reliability_summary" in MIGRATION
    for destructive in (
        "DROP TABLE",
        "DROP COLUMN",
        "TRUNCATE",
        "DELETE FROM",
        "UPDATE leads SET",
        "UPDATE flowpilot_idempotency SET",
    ):
        assert destructive not in MIGRATION


def test_audit_schema_is_explicit_and_append_oriented() -> None:
    for column in (
        "event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY",
        "occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()",
        "entity_type VARCHAR(16) NOT NULL",
        "entity_ref VARCHAR(80) NOT NULL",
        "event_type VARCHAR(48) NOT NULL",
        "previous_status VARCHAR(32)",
        "new_status VARCHAR(32)",
        "previous_stage VARCHAR(32)",
        "new_stage VARCHAR(32)",
    ):
        assert column in MIGRATION
    assert "JSON" not in MIGRATION


def test_event_names_are_fixed_and_machine_readable() -> None:
    expected = {
        "lead_created",
        "draft_pending_approval",
        "draft_approved",
        "draft_rejected",
        "idempotency_claimed",
        "idempotency_business_started",
        "idempotency_business_complete",
        "idempotency_completed",
        "idempotency_failed",
        "idempotency_recovery_required",
        "initial_email_sending",
        "initial_email_sent",
        "initial_email_failed",
        "initial_email_uncertain",
        "followup_scheduled",
        "followup_sending",
        "followup_sent",
        "followup_failed",
        "followup_uncertain",
        "followup_cancelled",
    }
    for event_type in expected:
        assert f"'{event_type}'" in MIGRATION


def test_idempotency_reference_is_sha256_not_plaintext() -> None:
    assert "sha256(convert_to(NEW.idempotency_key, 'UTF8'))" in MIGRATION
    assert "sha256(convert_to(idempotency_key, 'UTF8'))" in MIGRATION
    assert "request_ref TEXT := encode(" in MIGRATION
    assert "entity_ref, 'idempotency_key'" not in MIGRATION


def test_triggers_only_emit_for_real_state_changes() -> None:
    assert "OLD.draft_status IS DISTINCT FROM NEW.draft_status" in MIGRATION
    assert (
        "OLD.initial_response_delivery_status IS DISTINCT FROM "
        "NEW.initial_response_delivery_status"
    ) in MIGRATION
    assert "OLD.followup_status IS DISTINCT FROM NEW.followup_status" in MIGRATION
    assert "OLD.workflow_stage IS DISTINCT FROM NEW.workflow_stage" in MIGRATION
    assert "OLD.status IS DISTINCT FROM NEW.status" in MIGRATION


def test_recovery_run_schema_contains_only_nonnegative_counters() -> None:
    for column in (
        "initial_email_quarantined",
        "followups_quarantined",
        "stale_claimed_failed",
        "partial_work_recovery_required",
        "safely_completed",
        "malformed_complete_recovery_required",
        "legacy_recovery_required",
    ):
        assert f"{column} INTEGER NOT NULL DEFAULT 0" in MIGRATION
        assert f"CHECK ({column} >= 0)" in MIGRATION


def test_existing_recovery_node_records_exactly_one_summary() -> None:
    assert len(RECOVERY["nodes"]) == 2
    assert {node["type"] for node in RECOVERY["nodes"]} == {
        "n8n-nodes-base.scheduleTrigger",
        "n8n-nodes-base.postgres",
    }
    assert QUERY.count("INSERT INTO flowpilot_recovery_runs") == 1
    assert "FROM recovery_counts" in QUERY
    assert "FROM recorded_run" in QUERY


def test_recovery_thresholds_and_no_replay_policy_are_unchanged() -> None:
    assert QUERY.count("INTERVAL '30 minutes'") == 2
    assert QUERY.count("INTERVAL '60 minutes'") == 5
    assert QUERY.count("UPDATE leads") == 2
    assert QUERY.count("UPDATE flowpilot_idempotency") == 5
    serialized = json.dumps(RECOVERY).lower()
    for forbidden in ("httpRequest", "emailSend", "googleSheets", "hubspot", "openai"):
        assert forbidden.lower() not in serialized


def test_reconciliation_view_is_operator_friendly_and_read_only() -> None:
    for label in (
        "Needs review",
        "Initial email delivery uncertain",
        "Follow-up delivery uncertain",
    ):
        assert f"'{label}'" in MIGRATION
    for field in (
        "item_type",
        "item_ref",
        "display_status",
        "technical_status",
        "technical_stage",
        "requires_action",
        "state_since",
        "age_seconds",
    ):
        assert field in MIGRATION
    view_sql = MIGRATION.split(
        "CREATE OR REPLACE VIEW flowpilot_reconciliation_queue AS", 1
    )[1].split("CREATE OR REPLACE VIEW flowpilot_reliability_summary AS", 1)[0]
    for forbidden in (
        "lead.email",
        "lead.message",
        "draft_subject",
        "draft_body",
        "approval_token",
        "response_payload",
        "idempotency_key::TEXT AS item_ref",
    ):
        assert forbidden not in view_sql


def test_summary_view_has_small_stable_contract() -> None:
    for field in (
        "items_needing_reconciliation",
        "uncertain_initial_emails",
        "uncertain_followups",
        "processing_idempotency_records",
        "latest_recovery_run_at",
    ):
        assert field in MIGRATION


def test_observability_files_contain_no_secret_payloads() -> None:
    text = (MIGRATION + RECOVERY_PATH.read_text(encoding="utf-8")).lower()
    for forbidden in (
        "postgres_password",
        "smtp_password",
        "oauth_token",
        "api_key",
        "client_secret",
        "begin private key",
    ):
        assert forbidden not in text
    for node in RECOVERY["nodes"]:
        for reference in node.get("credentials", {}).values():
            assert set(reference) == {"id", "name"}


def test_workflow_inventory_remains_exactly_four() -> None:
    assert {path.name for path in (ROOT / "n8n").glob("*.json")} == {
        "flowpilot-lead-workflow.json",
        "flowpilot-approval-workflow.json",
        "flowpilot-followup-workflow.json",
        "flowpilot-recovery-workflow.json",
    }
