"""Structural tests for the version-controlled n8n workflow export."""

import json
from pathlib import Path

WORKFLOW_PATH = Path(__file__).parents[1] / "n8n" / "flowpilot-lead-workflow.json"


def load_workflow() -> dict[str, object]:
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def nodes_by_name(workflow: dict[str, object]) -> dict[str, dict[str, object]]:
    nodes = workflow["nodes"]
    assert isinstance(nodes, list)
    return {node["name"]: node for node in nodes}


def test_workflow_maps_exact_lead_contract() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)

    webhook = nodes["Lead Intake Webhook"]
    assert webhook["parameters"] == {
        "httpMethod": "POST",
        "path": "flowpilot/lead",
        "responseMode": "responseNode",
        "options": {},
    }

    assignments = nodes["Prepare Lead"]["parameters"]["assignments"]["assignments"]
    assert {assignment["name"] for assignment in assignments} == {
        "name",
        "email",
        "company",
        "message",
        "budget",
    }
    assert all("$json.body." in assignment["value"] for assignment in assignments)


def test_workflow_calls_flowpilot_through_configurable_url() -> None:
    workflow = load_workflow()
    request_node = nodes_by_name(workflow)["Analyze Lead with FlowPilot"]
    parameters = request_node["parameters"]

    assert request_node["type"] == "n8n-nodes-base.httpRequest"
    assert parameters["method"] == "POST"
    assert "FLOWPILOT_API_URL" in parameters["url"]
    assert "host.docker.internal:8000" in parameters["url"]
    assert "/lead/analyze" in parameters["url"]
    assert parameters["sendBody"] is True
    assert parameters["specifyBody"] == "json"
    assert request_node["onError"] == "continueRegularOutput"


def test_workflow_has_three_priority_routes_and_clean_error_route() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    switch = nodes["Route by Priority"]
    rules = switch["parameters"]["rules"]["values"]

    assert [rule["outputKey"] for rule in rules] == ["high", "medium", "low"]
    assert switch["parameters"]["options"]["fallbackOutput"] == "extra"

    outputs = workflow["connections"]["Route by Priority"]["main"]
    assert [output[0]["node"] for output in outputs] == [
        "Prepare High Response",
        "Prepare Medium Response",
        "Prepare Low Response",
        "Respond Error",
    ]

    validation_code = nodes["Validate API Result"]["parameters"]["jsCode"]
    assert "FLOWPILOT_UNAVAILABLE" in validation_code
    assert "VALIDATION_ERROR" in validation_code
    assert "INVALID_FLOWPILOT_RESPONSE" in validation_code


def test_workflow_contains_no_credentials_or_sample_customer_data() -> None:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "john@example.com" not in workflow_text.lower()
    assert "acme industries" not in workflow_text.lower()
    assert "api_key" not in workflow_text.lower()
    assert workflow_text.count("n8n-nodes-base.respondToWebhook") == 2
