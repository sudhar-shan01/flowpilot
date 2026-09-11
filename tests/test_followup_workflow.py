"""Execute the committed Code nodes and inspect the Phase 7 safety boundary.

Node.js is needed for n8n Code-node tests. No services or credentials are used.
PostgreSQL execution checks are separated in tests/integration/.
"""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.test_human_approval_workflow import first_target, reachable

ROOT = Path(__file__).parents[1]
WORKFLOW = json.loads((ROOT / "n8n/flowpilot-followup-workflow.json").read_text())
NODES = {node["name"]: node for node in WORKFLOW["nodes"]}
APPROVAL = json.loads((ROOT / "n8n/flowpilot-approval-workflow.json").read_text())
APPROVAL_NODES = {node["name"]: node for node in APPROVAL["nodes"]}
MIGRATION = (ROOT / "postgres/init/004_add_followups.sql").read_text()
CLAIM = NODES["Atomically Claim Due Follow-ups"]["parameters"]["query"]


def run_code(name, data, prepared=None, sender="team@example.com"):
    node = shutil.which("node")
    assert node, "Install Node.js to run the n8n Code-node regression tests"
    program = r"""
const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = {
  $json: input.data,
  $env: {FLOWPILOT_LEAD_EMAIL_FROM: input.sender},
  $: name => {
    if (name !== 'Prepare Follow-up Email') throw new Error('Unexpected source');
    return {item: {json: input.prepared}};
  },
};
const result = vm.runInNewContext('(function(){' + input.code + '\n})()', context, {timeout: 1000});
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"code": NODES[name]["parameters"]["jsCode"],
                         "data": data, "prepared": prepared, "sender": sender}),
        capture_output=True, text=True, check=True, timeout=10,
    )
    return json.loads(result.stdout)["json"]


def claimed(lead_id="1", email="lead@example.com"):
    return {"lead_id": lead_id, "email": email, "name": "Example",
            "claim_time": "2026-09-10 10:00:00.123456+00"}


def test_migration_is_additive_transactional_and_constrains_states():
    for field in ("followup_status VARCHAR(9)", "followup_due_at TIMESTAMPTZ",
                  "followup_claimed_at TIMESTAMPTZ", "followup_sent_at TIMESTAMPTZ"):
        assert f"ADD COLUMN IF NOT EXISTS {field}" in MIGRATION
    assert MIGRATION.startswith("BEGIN;")
    assert MIGRATION.rstrip().endswith("COMMIT;")
    assert "followup_status IS NULL" in MIGRATION
    assert "('scheduled', 'sending', 'sent', 'failed', 'cancelled')" in MIGRATION
    assert "CREATE INDEX IF NOT EXISTS" in MIGRATION
    for destructive in ("DROP TABLE", "DELETE FROM", "TRUNCATE", "UPDATE leads"):
        assert destructive not in MIGRATION


def test_scheduling_is_the_same_update_after_initial_smtp_success():
    query = APPROVAL_NODES["Record Response Sent Timestamp"]["parameters"]["query"]
    assert query.count("UPDATE leads") == 1
    assert "initial_response_sent_at = NOW()" in query
    assert "initial_response_delivery_status = 'sent'" in query
    assert "initial_response_delivery_status = 'sending'" in query
    assert "initial_response_claim_token = $2::uuid" in query
    assert "NOW() + INTERVAL '72 hours'" in query
    assert "followup_status IS NULL THEN 'scheduled' ELSE followup_status" in query
    assert "initial_response_sent_at IS NULL" in query
    assert "draft_status = 'approved'" in query
    assert "email" not in query.lower()
    assert first_target(APPROVAL, "Route Initial SMTP Result", 0) == "Record Response Sent Timestamp"
    assert "Record Response Sent Timestamp" not in reachable(APPROVAL, "Respond Email Failure")
    assert "Record Response Sent Timestamp" not in reachable(APPROVAL, "Approval Review Webhook")
    for node in APPROVAL["nodes"]:
        if node["name"] != "Record Response Sent Timestamp":
            assert "followup_status" not in node["parameters"].get("query", "")


def test_hourly_schedule_and_atomic_bounded_claim():
    schedule = NODES["Hourly Follow-up Schedule"]
    assert schedule["type"] == "n8n-nodes-base.scheduleTrigger"
    assert schedule["parameters"]["rule"]["interval"] == [
        {"field": "hours", "hoursInterval": 1, "triggerAtMinute": 0}]
    assert first_target(WORKFLOW, schedule["name"]) == "Atomically Claim Due Follow-ups"
    assert "FOR UPDATE SKIP LOCKED" in CLAIM
    assert "LIMIT 20" in CLAIM
    assert "UPDATE leads AS lead" in CLAIM
    assert "FROM due" in CLAIM
    assert "SET followup_status = 'sending'" in CLAIM
    assert "followup_claimed_at = NOW()" in CLAIM
    assert "RETURNING lead.id::text" in CLAIM
    assert "lead.followup_claimed_at::text AS claim_time" in CLAIM
    assert "followup_due_at <= NOW()" in CLAIM
    assert "followup_due_at >= initial_response_sent_at + INTERVAL '72 hours'" in CLAIM


@pytest.mark.parametrize("status", ["sent", "failed", "sending", "cancelled", "uncertain"])
def test_claim_accepts_only_scheduled_status(status):
    eligibility = CLAIM.split("ORDER BY")[0]
    assert "followup_status = 'scheduled'" in eligibility
    assert f"'{status}'" not in eligibility
    assert "followup_status IS NULL" not in eligibility


def test_claim_requires_approved_sent_initial_email_and_valid_persisted_recipient():
    assert "draft_status = 'approved'" in CLAIM
    assert "initial_response_sent_at IS NOT NULL" in CLAIM
    assert "followup_sent_at IS NULL" in CLAIM
    assert "AND email ~" in CLAIM
    assert "$json" not in CLAIM
    assert "$env" not in CLAIM
    assert NODES["Atomically Claim Due Follow-ups"].get("alwaysOutputData", False) is False


@pytest.mark.parametrize("recipient", [None, "", "   ", "invalid", "a@b", "a,b@example.com",
                                      "x@example.com\r\nBcc: other@example.com", "<a@example.com>"])
def test_invalid_persisted_recipients_never_become_ready(recipient):
    result = run_code("Prepare Follow-up Email", claimed(email=recipient))
    assert result["outcome"] == "failed"
    assert "email" not in result
    assert result["leadId"] == "1"
    assert result["claimTime"].endswith("123456+00")


def test_plain_text_content_is_fixed_and_recipient_is_only_from_claim():
    row = claimed()
    row.update({"name": "Injected\r\nHeader", "subject": "discount", "recipient": "attacker@example.com",
                "query": {"email": "attacker@example.com"}, "body": "unreviewed promise"})
    result = run_code("Prepare Follow-up Email", row)
    assert result["outcome"] == "ready"
    assert result["email"] == {
        "sender": "team@example.com", "recipient": "lead@example.com",
        "subject": "Following up on our previous message",
        "body": "Hello,\n\nJust following up on our previous email. If you'd like to continue the conversation, feel free to reply when convenient.",
    }
    assert "\r" not in result["email"]["subject"] and "\n" not in result["email"]["subject"]
    smtp = NODES["Send Follow-up Email"]["parameters"]
    assert smtp["emailFormat"] == "text"
    assert smtp["toEmail"] == "={{ $json.email.recipient }}"


@pytest.mark.parametrize("sender", ["", " ", "bad", "a@example.com\nBcc: b@example.com"])
def test_invalid_sender_suppresses_smtp(sender):
    assert run_code("Prepare Follow-up Email", claimed(), sender=sender)["outcome"] == "failed"


def test_multiple_items_keep_their_own_claim_and_recipient():
    for number in range(1, 21):
        result = run_code("Prepare Follow-up Email", claimed(str(number), f"lead{number}@example.com"))
        smtp = run_code("Sanitize Follow-up SMTP Result", {"accepted": [result["email"]["recipient"]]}, result)
        assert smtp == {"leadId": str(number), "claimTime": result["claimTime"], "outcome": "sent"}
    for node in NODES.values():
        if node["type"] == "n8n-nodes-base.code":
            assert node["parameters"]["mode"] == "runOnceForEachItem"
    for name in ("Mark Follow-up Sent", "Mark Follow-up Failed"):
        assert NODES[name]["parameters"]["options"]["queryBatching"] == "independently"


@pytest.mark.parametrize("response", [
    {"error": "password and host must stay internal"}, {}, {"success": False},
    {"accepted": []}, {"accepted": ["other@example.com"]},
    {"accepted": ["lead@example.com"], "rejected": ["lead@example.com"]},
])
def test_ambiguous_smtp_result_marks_uncertain_without_raw_details(response):
    prepared = run_code("Prepare Follow-up Email", claimed())
    result = run_code("Sanitize Follow-up SMTP Result", response, prepared)
    assert result == {"leadId": "1", "claimTime": prepared["claimTime"], "outcome": "uncertain"}


def test_explicit_recipient_rejection_is_failed_and_acceptance_is_sent():
    prepared = run_code("Prepare Follow-up Email", claimed())
    rejected = run_code(
        "Sanitize Follow-up SMTP Result",
        {"accepted": [], "rejected": ["lead@example.com"]},
        prepared,
    )
    accepted = run_code(
        "Sanitize Follow-up SMTP Result",
        {"accepted": ["lead@example.com"], "rejected": []},
        prepared,
    )
    assert rejected["outcome"] == "failed"
    assert accepted["outcome"] == "sent"


def test_updates_require_same_claim_and_cannot_reschedule_or_alter_initial_state():
    for name, status in (
        ("Mark Follow-up Sent", "sent"),
        ("Mark Follow-up Failed", "failed"),
        ("Mark Follow-up Uncertain", "uncertain"),
    ):
        node = NODES[name]
        query = node["parameters"]["query"]
        assert f"followup_status = '{status}'" in query
        assert "followup_status = 'sending'" in query
        assert "followup_claimed_at = $2::timestamptz" in query
        assert node["parameters"]["options"]["queryReplacement"] == "={{ [$json.leadId, $json.claimTime] }}"
        assert "draft_status" not in query and "initial_response_sent_at" not in query
        assert "scheduled" not in query
    assert "followup_sent_at = NOW()" in NODES["Mark Follow-up Sent"]["parameters"]["query"]
    assert "followup_sent_at" not in NODES["Mark Follow-up Failed"]["parameters"]["query"]
    assert first_target(WORKFLOW, "Route Follow-up SMTP Result", 0) == "Mark Follow-up Sent"
    assert first_target(WORKFLOW, "Route Follow-up SMTP Result", 1) == "Mark Follow-up Failed"
    assert first_target(WORKFLOW, "Route Follow-up SMTP Result", 2) == "Mark Follow-up Uncertain"
    assert "Send Follow-up Email" not in reachable(WORKFLOW, "Mark Follow-up Failed")


def test_claim_and_state_errors_are_sanitized():
    raw = {"error": "database host, password, SQL stack"}
    assert run_code("Prepare Follow-up Email", raw)["error"]["code"] == "FOLLOWUP_ERROR"
    for response in (raw, {"recorded": False}, {}):
        result = run_code("Sanitize Follow-up State Result", response)
        assert result["error"]["code"] == "FOLLOWUP_STATE_ERROR"
        assert "password" not in json.dumps(result)
    for state in ("sent", "failed", "uncertain"):
        assert run_code(
            "Sanitize Follow-up State Result",
            {"recorded": True, "delivery_status": state},
        ) == {"outcome": state}


def test_exports_have_no_ai_webhooks_retries_or_credential_payloads():
    for node in NODES.values():
        assert node["type"] in {"n8n-nodes-base.scheduleTrigger", "n8n-nodes-base.postgres",
                                "n8n-nodes-base.code", "n8n-nodes-base.switch", "n8n-nodes-base.emailSend"}
        assert not node.get("retryOnFail", False)
        for reference in node.get("credentials", {}).values():
            assert set(reference) == {"id", "name"}
    assert WORKFLOW["active"] is False
    assert WORKFLOW["pinData"] == {}
    serialized = json.dumps(WORKFLOW).lower()
    for secret in ("postgres_password", "smtp_password", "oauth", "openai", "api_key"):
        assert secret not in serialized
    assert "@" not in NODES["Send Follow-up Email"]["parameters"]["fromEmail"]
