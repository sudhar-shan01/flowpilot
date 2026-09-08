"""Static verification for Phase 5 draft persistence and internal email."""

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKFLOW_PATH = ROOT / "n8n" / "flowpilot-lead-workflow.json"
MIGRATION_PATH = ROOT / "postgres" / "init" / "002_add_lead_drafts.sql"


def load_workflow() -> dict[str, object]:
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def nodes_by_name(workflow: dict[str, object]) -> dict[str, dict[str, object]]:
    nodes = workflow["nodes"]
    assert isinstance(nodes, list)
    return {node["name"]: node for node in nodes}


def first_target(workflow: dict[str, object], source: str, output: int = 0) -> str:
    return workflow["connections"][source]["main"][output][0]["node"]


def test_draft_migration_is_additive_and_requires_pending_approval() -> None:
    migration = MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "alter table leads" in migration
    assert "add column if not exists draft_subject" in migration
    assert "add column if not exists draft_body" in migration
    assert "add column if not exists draft_status" in migration
    assert "pending_approval" in migration
    assert "drop table" not in migration


def test_draft_runs_after_hubspot_and_persists_before_notification() -> None:
    workflow = load_workflow()

    expected_path = [
        ("Sanitize HubSpot Result", "Generate Lead Response Draft"),
        ("Generate Lead Response Draft", "Validate Draft Result"),
        ("Validate Draft Result", "Persist Only Valid Draft"),
        ("Persist Only Valid Draft", "Persist Draft in PostgreSQL"),
        ("Persist Draft in PostgreSQL", "Sanitize Draft Persistence"),
        ("Sanitize Draft Persistence", "Notify Only After Draft Persistence"),
        ("Notify Only After Draft Persistence", "Prepare Internal Notification"),
        ("Prepare Internal Notification", "Send Configured Internal Email"),
        ("Send Configured Internal Email", "Send Internal Notification"),
        ("Send Internal Notification", "Sanitize Email Result"),
        ("Sanitize Email Result", "Route After Email Result"),
        ("Route After Email Result", "Route by Priority"),
    ]
    for source, target in expected_path:
        assert first_target(workflow, source) == target


def test_draft_api_call_and_persistence_are_structured_and_parameterized() -> None:
    nodes = nodes_by_name(load_workflow())
    request = nodes["Generate Lead Response Draft"]["parameters"]
    persistence = nodes["Persist Draft in PostgreSQL"]["parameters"]

    assert request["url"].endswith("+ '/lead/draft' }}")
    assert "lead: $json.lead" in request["jsonBody"]
    assert "analysis: $json.analysis" in request["jsonBody"]
    assert "$1" in persistence["query"]
    assert "$4" in persistence["query"]
    assert "draft_subject" in persistence["query"]
    assert "draft_body" in persistence["query"]
    assert "draft_status" in persistence["query"]
    assert "'pending_approval'" in persistence["options"]["queryReplacement"]
    assert "$json.lead_id" in persistence["options"]["queryReplacement"]


def test_draft_failures_are_sanitized_and_bypass_email() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    error_code = nodes["Validate Draft Result"]["parameters"]["jsCode"]
    persistence_error = nodes["Sanitize Draft Persistence"]["parameters"]["jsCode"]

    assert "DRAFT_ERROR" in error_code
    assert "DRAFT_ERROR" in persistence_error
    assert "statusCode: 503" in error_code
    assert "provider" not in error_code.lower()
    assert first_target(workflow, "Persist Only Valid Draft", 1) == "Respond Error"
    assert first_target(workflow, "Notify Only After Draft Persistence", 1) == (
        "Respond Error"
    )


def test_internal_notification_is_plain_text_and_recipient_is_trusted() -> None:
    nodes = nodes_by_name(load_workflow())
    prepare = nodes["Prepare Internal Notification"]["parameters"]["jsCode"]
    email = nodes["Send Internal Notification"]

    assert "$env.FLOWPILOT_INTERNAL_EMAIL_TO" in prepare
    assert "data.draft.subject" in prepare
    assert "data.draft.body" in prepare
    assert email["type"] == "n8n-nodes-base.emailSend"
    assert email["parameters"]["emailFormat"] == "text"
    assert email["parameters"]["toEmail"] == "={{ $json.notification.recipient }}"
    assert "$env.FLOWPILOT_INTERNAL_EMAIL_FROM" in email["parameters"]["fromEmail"]
    assert "recipient" not in nodes["Generate Lead Response Draft"]["parameters"][
        "jsonBody"
    ]


def test_email_failure_is_sanitized_and_cannot_reach_success_route() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    sanitizer = nodes["Sanitize Email Result"]
    code = sanitizer["parameters"]["jsCode"]

    assert nodes["Send Internal Notification"]["onError"] == "continueRegularOutput"
    assert "EMAIL_ERROR" in code
    assert "statusCode: 503" in code
    assert "credential" not in code.lower()
    assert first_target(workflow, "Send Configured Internal Email", 1) == (
        "Respond Error"
    )
    assert first_target(workflow, "Route After Email Result", 1) == "Respond Error"


def test_email_export_contains_reference_metadata_only_and_no_recipient() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    credential = nodes["Send Internal Notification"]["credentials"]["smtp"]
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8").lower()

    assert set(credential) == {"id", "name"}
    assert credential["name"] == "FlowPilot Email"
    assert "smtp_password" not in workflow_text
    assert "oauth_token" not in workflow_text
    assert "app_password" not in workflow_text
    assert "@gmail.com" not in workflow_text


def test_public_success_response_contract_is_unchanged() -> None:
    success = nodes_by_name(load_workflow())["Respond Success"]["parameters"]

    assert success["responseBody"] == (
        "={{ { success: true, route: $json.route, message: $json.message, "
        "analysis: $json.analysis } }}"
    )
    assert success["options"]["responseCode"] == 200
