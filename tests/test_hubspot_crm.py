"""Static verification for the Phase 4 HubSpot contact synchronization.

These tests require neither HubSpot credentials nor network access.
"""

import json
from pathlib import Path

WORKFLOW_PATH = Path(__file__).parents[1] / "n8n" / "flowpilot-lead-workflow.json"


def load_workflow() -> dict[str, object]:
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def nodes_by_name(workflow: dict[str, object]) -> dict[str, dict[str, object]]:
    nodes = workflow["nodes"]
    assert isinstance(nodes, list)
    return {node["name"]: node for node in nodes}


def first_target(workflow: dict[str, object], source: str, output: int = 0) -> str:
    return workflow["connections"][source]["main"][output][0]["node"]


def test_hubspot_sync_runs_after_sheets_and_before_priority_routing() -> None:
    workflow = load_workflow()

    assert first_target(workflow, "Sanitize Google Sheets Result") == (
        "Sync HubSpot Only After Persistence"
    )
    assert first_target(workflow, "Sync HubSpot Only After Persistence") == (
        "Prepare HubSpot Contact"
    )
    assert first_target(workflow, "Prepare HubSpot Contact") == (
        "Search HubSpot Contact by Email"
    )
    assert first_target(workflow, "Search HubSpot Contact by Email") == (
        "Determine HubSpot Contact Path"
    )
    assert first_target(workflow, "Sanitize HubSpot Result") == (
        "Generate Lead Response Draft"
    )


def test_hubspot_search_uses_email_as_the_unique_lookup_key() -> None:
    search = nodes_by_name(load_workflow())["Search HubSpot Contact by Email"]
    parameters = search["parameters"]
    body = parameters["jsonBody"]

    assert search["type"] == "n8n-nodes-base.httpRequest"
    assert parameters["method"] == "POST"
    assert parameters["url"].endswith("/crm/v3/objects/contacts/search")
    assert parameters["authentication"] == "predefinedCredentialType"
    assert parameters["nodeCredentialType"] == "hubspotOAuth2Api"
    assert "propertyName: 'email'" in body
    assert "operator: 'EQ'" in body
    assert "$json.crmContact.email" in body
    assert "limit: 1" in body


def test_existing_contact_updates_and_missing_contact_creates() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    decision_code = nodes["Determine HubSpot Contact Path"]["parameters"][
        "jsCode"
    ]
    branch = nodes["Choose HubSpot Create or Update"]

    assert "result.results.length === 0" in decision_code
    assert "action: 'create'" in decision_code
    assert "result.results[0]?.id" in decision_code
    assert "action: 'update'" in decision_code

    outputs = workflow["connections"][branch["name"]]["main"]
    assert [output[0]["node"] for output in outputs] == [
        "Update HubSpot Contact",
        "Create HubSpot Contact",
        "Prepare Idempotency Failure",
    ]

    update = nodes["Update HubSpot Contact"]["parameters"]
    create = nodes["Create HubSpot Contact"]["parameters"]
    assert update["method"] == "PATCH"
    assert "$json.crmContact.contactId" in update["url"]
    assert create["method"] == "POST"
    assert create["url"].endswith("/crm/v3/objects/contacts")


def test_hubspot_contact_maps_email_name_and_company() -> None:
    nodes = nodes_by_name(load_workflow())
    prepare_code = nodes["Prepare HubSpot Contact"]["parameters"]["jsCode"]

    assert "persisted.lead.name" in prepare_code
    assert "split(/\\s+/)" in prepare_code
    assert "firstname" in prepare_code
    assert "lastname" in prepare_code
    assert "email: persisted.lead.email" in prepare_code
    assert "company: persisted.lead.company" in prepare_code

    for node_name in ("Update HubSpot Contact", "Create HubSpot Contact"):
        body = nodes[node_name]["parameters"]["jsonBody"]
        for field in ("email", "firstname", "lastname", "company"):
            assert f"$json.crmContact.{field}" in body
        assert "$json.analysis" not in body


def test_previous_persistence_failures_bypass_hubspot() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    gate_name = "Sync HubSpot Only After Persistence"
    gate = nodes[gate_name]
    condition = gate["parameters"]["rules"]["values"][0]["conditions"][
        "conditions"
    ][0]["leftValue"]

    assert "$json.success === true" in condition
    assert "$json.created_at" in condition
    assert first_target(workflow, gate_name, 0) == "Prepare HubSpot Contact"
    assert first_target(workflow, gate_name, 1) == "Prepare Idempotency Failure"
    assert first_target(
        workflow, "Persist to Google Sheets Only After PostgreSQL", 1
    ) == "Prepare Idempotency Failure"


def test_hubspot_failures_return_only_sanitized_crm_error() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)

    for node_name in (
        "Search HubSpot Contact by Email",
        "Update HubSpot Contact",
        "Create HubSpot Contact",
    ):
        assert nodes[node_name]["onError"] == "continueRegularOutput"

    error_codes = "\n".join(
        nodes[node_name]["parameters"]["jsCode"]
        for node_name in (
            "Determine HubSpot Contact Path",
            "Sanitize HubSpot Result",
        )
    )
    assert "CRM_ERROR" in error_codes
    assert "statusCode: 503" in error_codes
    assert "Lead was persisted but could not be synchronized with CRM." in error_codes
    for internal_detail in ("oauth", "token", "account", "stack", "api.hubapi"):
        assert internal_detail not in error_codes.lower()

    response = nodes["Respond Error"]["parameters"]["responseBody"]
    assert response == "={{ { success: false, error: $json.error } }}"


def test_hubspot_export_has_no_secret_and_success_contract_is_unchanged() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    credential_nodes = (
        "Search HubSpot Contact by Email",
        "Update HubSpot Contact",
        "Create HubSpot Contact",
    )

    references = []
    for node_name in credential_nodes:
        reference = nodes[node_name]["credentials"]["hubspotOAuth2Api"]
        assert set(reference) == {"id", "name"}
        assert reference["name"] == "FlowPilot HubSpot"
        references.append(reference)
    assert references[0] == references[1] == references[2]

    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8").lower()
    for secret_key in (
        "access_token",
        "refresh_token",
        "client_secret",
        "private_app_token",
    ):
        assert secret_key not in workflow_text

    success = nodes["Respond Success"]["parameters"]
    assert success["responseBody"] == (
        "={{ { success: true, route: $json.route, message: $json.message, "
        "analysis: $json.analysis } }}"
    )
    assert success["options"]["responseCode"] == 200
