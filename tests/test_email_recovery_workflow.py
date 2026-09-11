"""Static and Code-node regression tests for Phase 8B1 email recovery."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.test_human_approval_workflow import first_target, reachable


ROOT = Path(__file__).parents[1]
MIGRATION = (ROOT / "postgres/init/006_add_email_recovery.sql").read_text()
APPROVAL = json.loads((ROOT / "n8n/flowpilot-approval-workflow.json").read_text())
FOLLOWUP = json.loads((ROOT / "n8n/flowpilot-followup-workflow.json").read_text())
RECOVERY = json.loads((ROOT / "n8n/flowpilot-recovery-workflow.json").read_text())
APPROVAL_NODES = {node["name"]: node for node in APPROVAL["nodes"]}
FOLLOWUP_NODES = {node["name"]: node for node in FOLLOWUP["nodes"]}
RECOVERY_NODES = {node["name"]: node for node in RECOVERY["nodes"]}


def run_code(nodes, name, data, *, prepared=None, sender="team@example.com"):
    node = shutil.which("node")
    assert node, "Install Node.js to run n8n Code-node regression tests"
    program = r"""
const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = {
  $json: input.data,
  $env: {FLOWPILOT_LEAD_EMAIL_FROM: input.sender},
  $: name => ({item: {json: input.prepared}}),
};
const result = vm.runInNewContext('(function(){' + input.code + '\n})()', context, {timeout: 1000});
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps(
            {
                "code": nodes[name]["parameters"]["jsCode"],
                "data": data,
                "prepared": prepared,
                "sender": sender,
            }
        ),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    parsed = json.loads(result.stdout)
    return parsed[0]["json"] if isinstance(parsed, list) else parsed["json"]


def claimed_initial():
    return {
        "claimResult": "owner",
        "leadId": "1",
        "claimToken": "00000000-0000-4000-8000-000000000001",
        "claimedAt": "2026-09-11T10:00:00.000Z",
        "persisted": {
            "recipient": "lead@example.com",
            "subject": "Your FlowPilot request",
            "body": "Hello,\n\nThanks for contacting us.",
        },
    }


def test_migration_is_additive_transactional_rerunnable_and_preserves_nulls():
    assert MIGRATION.startswith("BEGIN;")
    assert MIGRATION.rstrip().endswith("COMMIT;")
    for column in (
        "initial_response_delivery_status VARCHAR(9)",
        "initial_response_claim_token UUID",
        "initial_response_claimed_at TIMESTAMPTZ",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in MIGRATION
    assert "initial_response_delivery_status IS NULL" in MIGRATION
    assert "followup_status IS NULL" in MIGRATION
    assert "('sending', 'sent', 'failed', 'uncertain')" in MIGRATION
    assert "('scheduled', 'sending', 'sent', 'failed', 'cancelled', 'uncertain')" in MIGRATION
    for destructive in ("DROP TABLE", "DELETE FROM", "TRUNCATE", "UPDATE leads"):
        assert destructive not in MIGRATION


def test_initial_response_claim_is_one_atomic_parameterized_transition():
    node = APPROVAL_NODES["Atomically Claim Initial Response"]
    query = node["parameters"]["query"]
    assert query.count("UPDATE leads") == 1
    assert "gen_random_uuid() AS attempted_claim_token" in query
    assert "initial_response_delivery_status IS NULL" in query
    assert "initial_response_sent_at IS NULL" in query
    assert "draft_status = 'approved'" in query
    assert "THEN 'sending'" in query
    assert "ELSE 'failed'" in query
    assert node["parameters"]["options"]["queryReplacement"] == "={{ [$json.leadId] }}"
    assert "$json" not in query and "$env" not in query
    assert node["onError"] == "continueRegularOutput"


def test_initial_claim_requires_valid_persisted_recipient_subject_and_body():
    query = APPROVAL_NODES["Atomically Claim Initial Response"]["parameters"]["query"]
    assert "lead.email ~" in query
    assert "NULLIF(BTRIM(lead.draft_subject), '') IS NOT NULL" in query
    assert "lead.draft_subject !~ '[\\r\\n]'" in query
    assert "NULLIF(BTRIM(lead.draft_body), '') IS NOT NULL" in query
    for field in ("lead.email", "lead.draft_subject", "lead.draft_body"):
        assert field in query


def test_only_claim_owner_can_reach_initial_smtp():
    assert first_target(APPROVAL, "Route Approval Result", 0) == (
        "Atomically Claim Initial Response"
    )
    assert first_target(APPROVAL, "Route Initial Send Claim", 0) == (
        "Prepare Approved Email"
    )
    assert first_target(APPROVAL, "Route Initial Send Claim", 1) == (
        "Respond Email Failure"
    )
    assert first_target(APPROVAL, "Route Initial Send Claim", 2) == (
        "Respond Approval Error"
    )
    for output in (1, 2):
        start = first_target(APPROVAL, "Route Initial Send Claim", output)
        assert "Send Persisted Draft to Lead" not in reachable(APPROVAL, start)


def test_initial_email_uses_claimed_persisted_content_and_configured_sender_only():
    prepared = run_code(
        APPROVAL_NODES, "Prepare Approved Email", claimed_initial()
    )
    assert prepared["approvalResult"] == "email_ready"
    assert prepared["email"] == {
        "sender": "team@example.com",
        "recipient": "lead@example.com",
        "subject": "Your FlowPilot request",
        "body": "Hello,\n\nThanks for contacting us.",
    }
    smtp = APPROVAL_NODES["Send Persisted Draft to Lead"]["parameters"]
    assert smtp["toEmail"] == "={{ $json.email.recipient }}"
    assert smtp["subject"] == "={{ $json.email.subject }}"
    assert smtp["text"] == "={{ $json.email.body }}"


@pytest.mark.parametrize("sender", ["", "bad", "x@example.com\nBcc:y@example.com"])
def test_invalid_sender_fails_before_initial_smtp(sender):
    prepared = run_code(
        APPROVAL_NODES,
        "Prepare Approved Email",
        claimed_initial(),
        sender=sender,
    )
    assert prepared == {
        "leadId": "1",
        "claimToken": "00000000-0000-4000-8000-000000000001",
        "approvalResult": "pre_smtp_failure",
    }
    assert first_target(APPROVAL, "Send Only Configured Lead Email", 1) == (
        "Mark Initial Response Failed"
    )


def test_initial_smtp_results_are_classified_conservatively():
    prepared = run_code(
        APPROVAL_NODES, "Prepare Approved Email", claimed_initial()
    )
    cases = [
        ({"accepted": ["lead@example.com"], "rejected": []}, "sent"),
        ({"accepted": [], "rejected": ["lead@example.com"]}, "failed"),
        ({"error": "connection lost"}, "uncertain"),
        ({}, "uncertain"),
        ({"success": False}, "uncertain"),
        ({"accepted": ["other@example.com"]}, "uncertain"),
        ({"accepted": ["lead@example.com"], "rejected": ["lead@example.com"]}, "uncertain"),
    ]
    for result, expected in cases:
        sanitized = run_code(
            APPROVAL_NODES,
            "Sanitize Lead Email Result",
            result,
            prepared=prepared,
        )
        assert sanitized["outcome"] == expected
        assert "connection lost" not in json.dumps(sanitized)


def test_initial_delivery_updates_require_original_claim_and_never_resend():
    for name, state in (
        ("Record Response Sent Timestamp", "sent"),
        ("Mark Initial Response Failed", "failed"),
        ("Mark Initial Response Uncertain", "uncertain"),
    ):
        node = APPROVAL_NODES[name]
        query = node["parameters"]["query"]
        assert f"initial_response_delivery_status = '{state}'" in query
        assert "initial_response_delivery_status = 'sending'" in query
        assert "initial_response_claim_token = $2::uuid" in query
        assert node["parameters"]["options"]["queryReplacement"] == (
            "={{ [$json.leadId, $json.claimToken] }}"
        )
        assert "Send Persisted Draft to Lead" not in reachable(APPROVAL, name)


def test_only_confirmed_initial_acceptance_sets_timestamp_and_schedules_followup():
    sent = APPROVAL_NODES["Record Response Sent Timestamp"]["parameters"]["query"]
    assert "initial_response_sent_at = NOW()" in sent
    assert "followup_status = CASE WHEN followup_status IS NULL THEN 'scheduled'" in sent
    assert "NOW() + INTERVAL '72 hours'" in sent
    for name in ("Mark Initial Response Failed", "Mark Initial Response Uncertain"):
        query = APPROVAL_NODES[name]["parameters"]["query"]
        assert "initial_response_sent_at =" not in query
        assert "followup_status" not in query


def test_initial_state_recording_errors_are_sanitized():
    for raw in ({"error": "database password"}, {}, {"recorded": False}):
        result = run_code(
            APPROVAL_NODES, "Sanitize Initial Delivery State", raw
        )
        assert result["deliveryResult"] == "error"
        assert result["error"]["code"] == "EMAIL_RECOVERY_ERROR"
        assert "password" not in json.dumps(result)
    for state in ("failed", "uncertain"):
        result = run_code(
            APPROVAL_NODES,
            "Sanitize Initial Delivery State",
            {"recorded": True, "delivery_status": state},
        )
        assert result["deliveryResult"] == state


def test_followup_ambiguous_result_routes_to_guarded_uncertain_state():
    assert first_target(FOLLOWUP, "Route Follow-up SMTP Result", 0) == (
        "Mark Follow-up Sent"
    )
    assert first_target(FOLLOWUP, "Route Follow-up SMTP Result", 1) == (
        "Mark Follow-up Failed"
    )
    assert first_target(FOLLOWUP, "Route Follow-up SMTP Result", 2) == (
        "Mark Follow-up Uncertain"
    )
    query = FOLLOWUP_NODES["Mark Follow-up Uncertain"]["parameters"]["query"]
    assert "followup_status = 'uncertain'" in query
    assert "followup_status = 'sending'" in query
    assert "followup_claimed_at = $2::timestamptz" in query
    assert "scheduled" not in query


def test_recovery_workflow_only_quarantines_stale_sending_rows():
    schedule = RECOVERY_NODES["Hourly Recovery Schedule"]
    quarantine = RECOVERY_NODES["Quarantine Stale Email Sends"]
    query = quarantine["parameters"]["query"]
    assert schedule["parameters"]["rule"]["interval"] == [
        {"field": "hours", "hoursInterval": 1, "triggerAtMinute": 15}
    ]
    assert first_target(RECOVERY, schedule["name"]) == quarantine["name"]
    assert query.count("UPDATE leads") == 2
    assert "initial_response_delivery_status = 'sending'" in query
    assert "followup_status = 'sending'" in query
    assert query.count("<= NOW() - INTERVAL '30 minutes'") == 2
    assert "initial_response_claim_token IS NOT NULL" in query
    assert "initial_response_claimed_at IS NOT NULL" in query
    assert "followup_claimed_at IS NOT NULL" in query
    assert query.count("= 'uncertain'") == 2


def test_recovery_workflow_has_no_send_or_external_side_effect_nodes():
    assert {node["type"] for node in RECOVERY["nodes"]} == {
        "n8n-nodes-base.scheduleTrigger",
        "n8n-nodes-base.postgres",
    }
    assert RECOVERY["active"] is False
    assert RECOVERY["pinData"] == {}
    assert RECOVERY["settings"]["timezone"] == "UTC"


def test_no_email_retry_or_uncertain_requeue_exists():
    for workflow in (APPROVAL, FOLLOWUP, RECOVERY):
        for node in workflow["nodes"]:
            assert not node.get("retryOnFail", False)
        serialized = json.dumps(workflow).lower()
        for forbidden in (
            "retry_count",
            "exponential",
            "uncertain' then 'scheduled",
            "uncertain' then 'sending",
        ):
            assert forbidden not in serialized


def test_approval_get_remains_side_effect_free_and_phase8a_graph_is_unchanged():
    get_path = {"Approval Review Webhook", *reachable(APPROVAL, "Approval Review Webhook")}
    for name in get_path:
        assert APPROVAL_NODES[name]["type"] not in {
            "n8n-nodes-base.postgres",
            "n8n-nodes-base.emailSend",
            "n8n-nodes-base.httpRequest",
        }
    lead_workflow = json.loads(
        (ROOT / "n8n/flowpilot-lead-workflow.json").read_text()
    )
    assert first_target(lead_workflow, "Route Idempotency Claim", 0) == "Prepare Lead"
    assert first_target(lead_workflow, "Route Idempotency Claim", 1) == (
        "Respond Idempotency Replay"
    )
    assert first_target(lead_workflow, "Route Idempotency Claim", 2) == (
        "Respond Idempotency Error"
    )


def test_exports_contain_only_credential_references_and_no_secrets():
    for workflow in (APPROVAL, FOLLOWUP, RECOVERY):
        for node in workflow["nodes"]:
            for reference in node.get("credentials", {}).values():
                assert set(reference) == {"id", "name"}
        serialized = json.dumps(workflow).lower()
        for secret in (
            "smtp_password",
            "postgres_password",
            "oauth_token",
            "app_password",
            "api_key",
        ):
            assert secret not in serialized
