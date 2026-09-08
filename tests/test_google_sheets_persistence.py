"""Static verification for the Phase 3B Google Sheets integration.

These tests require neither Google credentials nor network access.
"""

import json
from pathlib import Path

WORKFLOW_PATH = Path(__file__).parents[1] / "n8n" / "flowpilot-lead-workflow.json"

SHEET_COLUMNS = [
    "created_at",
    "name",
    "email",
    "company",
    "message",
    "budget",
    "category",
    "priority",
    "lead_score",
    "short_summary",
    "recommended_action",
]


def load_workflow() -> dict[str, object]:
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def nodes_by_name(workflow: dict[str, object]) -> dict[str, dict[str, object]]:
    nodes = workflow["nodes"]
    assert isinstance(nodes, list)
    return {node["name"]: node for node in nodes}


def first_target(workflow: dict[str, object], source: str, output: int = 0) -> str:
    return workflow["connections"][source]["main"][output][0]["node"]


def test_google_sheets_append_is_between_postgres_and_priority_routing() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    sheets = nodes["Append Lead to Google Sheets"]

    assert sheets["type"] == "n8n-nodes-base.googleSheets"
    assert sheets["typeVersion"] == 4.7
    assert sheets["parameters"]["resource"] == "sheet"
    assert sheets["parameters"]["operation"] == "append"
    assert sheets["onError"] == "continueRegularOutput"

    assert first_target(workflow, "Persist Lead in PostgreSQL") == (
        "Sanitize Persistence Result"
    )
    assert first_target(workflow, "Sanitize Persistence Result") == (
        "Persist to Google Sheets Only After PostgreSQL"
    )
    assert first_target(
        workflow, "Persist to Google Sheets Only After PostgreSQL"
    ) == "Append Lead to Google Sheets"
    assert first_target(workflow, "Append Lead to Google Sheets") == (
        "Sanitize Google Sheets Result"
    )
    assert first_target(workflow, "Sanitize Google Sheets Result") == (
        "Sync HubSpot Only After Persistence"
    )
    assert first_target(workflow, "Sanitize HubSpot Result") == (
        "Generate Lead Response Draft"
    )


def test_google_sheets_maps_the_postgres_timestamp_and_required_fields() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    sheets = nodes["Append Lead to Google Sheets"]
    columns = sheets["parameters"]["columns"]

    assert columns["mappingMode"] == "defineBelow"
    assert list(columns["value"]) == SHEET_COLUMNS
    assert [field["id"] for field in columns["schema"]] == SHEET_COLUMNS
    assert columns["value"] == {
        "created_at": "={{ $json.created_at }}",
        "name": "={{ $json.lead.name }}",
        "email": "={{ $json.lead.email }}",
        "company": "={{ $json.lead.company }}",
        "message": "={{ $json.lead.message }}",
        "budget": "={{ $json.lead.budget }}",
        "category": "={{ $json.analysis.category }}",
        "priority": "={{ $json.analysis.priority }}",
        "lead_score": "={{ $json.analysis.lead_score }}",
        "short_summary": "={{ $json.analysis.short_summary }}",
        "recommended_action": "={{ $json.analysis.recommended_action }}",
    }

    postgres_sanitizer = nodes["Sanitize Persistence Result"]["parameters"][
        "jsCode"
    ]
    assert "created_at: stored.created_at" in postgres_sanitizer


def test_postgres_failure_bypasses_google_sheets() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    gate_name = "Persist to Google Sheets Only After PostgreSQL"
    gate = nodes[gate_name]
    condition = gate["parameters"]["rules"]["values"][0]["conditions"][
        "conditions"
    ][0]

    assert "$json.success === true" in condition["leftValue"]
    assert "$json.created_at" in condition["leftValue"]
    assert gate["parameters"]["options"]["fallbackOutput"] == "extra"
    assert first_target(workflow, gate_name, 0) == "Append Lead to Google Sheets"
    assert first_target(workflow, gate_name, 1) == "Respond Error"


def test_google_sheets_failure_is_sanitized() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    sanitizer = nodes["Sanitize Google Sheets Result"]
    code = sanitizer["parameters"]["jsCode"]

    assert "sheetsResult.error" in code
    assert "PERSISTENCE_ERROR" in code
    assert "statusCode: 503" in code
    assert "Lead was analyzed but could not be fully persisted." in code
    for internal_detail in ("oauth", "spreadsheet", "credential", "stack", "database"):
        assert internal_detail not in code.lower()


def test_google_sheets_export_contains_only_credential_reference_metadata() -> None:
    workflow = load_workflow()
    sheets = nodes_by_name(workflow)["Append Lead to Google Sheets"]
    credential = sheets["credentials"]["googleSheetsOAuth2Api"]
    document = sheets["parameters"]["documentId"]
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8").lower()

    assert set(credential) == {"id", "name"}
    assert document["mode"] == "list"
    assert document["value"] == ""
    for secret_key in (
        "access_token",
        "refresh_token",
        "client_secret",
        "private_key",
        "service_account",
    ):
        assert secret_key not in workflow_text


def test_success_response_contract_remains_unchanged() -> None:
    workflow = load_workflow()
    response = nodes_by_name(workflow)["Respond Success"]["parameters"]

    assert response["responseBody"] == (
        "={{ { success: true, route: $json.route, message: $json.message, "
        "analysis: $json.analysis } }}"
    )
    assert response["options"]["responseCode"] == 200
