"""Static verification for the Phase 3A PostgreSQL integration.

These tests intentionally require neither PostgreSQL nor n8n to be running.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCHEMA_PATH = ROOT / "postgres" / "init" / "001_create_leads.sql"
COMPOSE_PATH = ROOT / "compose.postgres.yml"
WORKFLOW_PATH = ROOT / "n8n" / "flowpilot-lead-workflow.json"


def load_workflow() -> dict[str, object]:
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def nodes_by_name(workflow: dict[str, object]) -> dict[str, dict[str, object]]:
    nodes = workflow["nodes"]
    assert isinstance(nodes, list)
    return {node["name"]: node for node in nodes}


def test_postgres_schema_has_required_columns_and_constraints() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8").lower()

    for column in (
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
    ):
        assert re.search(rf"\b{column}\b", schema)

    assert "generated always as identity primary key" in schema
    assert "created_at timestamptz" in schema
    assert "default current_timestamp" in schema
    assert "priority in ('high', 'medium', 'low')" in schema
    assert "lead_score between 0 and 100" in schema


def test_local_postgres_configuration_uses_env_and_init_schema() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "postgres:17-alpine" in compose
    assert "./postgres/init:/docker-entrypoint-initdb.d:ro" in compose
    assert "${POSTGRES_PASSWORD:?" in compose
    assert "timezone=UTC" in compose
    assert "pg_isready" in compose


def test_n8n_postgres_node_uses_parameterized_required_field_mappings() -> None:
    postgres = nodes_by_name(load_workflow())["Persist Lead in PostgreSQL"]
    parameters = postgres["parameters"]
    query = parameters["query"]
    replacements = parameters["options"]["queryReplacement"]

    assert postgres["type"] == "n8n-nodes-base.postgres"
    assert postgres["typeVersion"] == 2.7
    assert postgres["onError"] == "continueRegularOutput"
    assert parameters["operation"] == "executeQuery"
    assert not re.search(r"values\s*\([^$]*['\"]", query, re.IGNORECASE)
    assert [f"${index}" in query for index in range(1, 11)] == [True] * 10

    expected_mappings = {
        "$json.lead.name",
        "$json.lead.email",
        "$json.lead.company",
        "$json.lead.message",
        "$json.lead.budget",
        "$json.analysis.category",
        "$json.analysis.priority",
        "$json.analysis.lead_score",
        "$json.analysis.short_summary",
        "$json.analysis.recommended_action",
    }
    assert all(mapping in replacements for mapping in expected_mappings)


def test_only_a_valid_analysis_can_reach_persistence() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    connections = workflow["connections"]
    gate = nodes["Persist Only Valid Analysis"]

    assert "$json.success" in gate["parameters"]["rules"]["values"][0][
        "conditions"
    ]["conditions"][0]["leftValue"]
    assert connections["Validate API Result"]["main"][0][0]["node"] == gate["name"]
    assert connections[gate["name"]]["main"][0][0]["node"] == (
        "Persist Lead in PostgreSQL"
    )
    assert connections[gate["name"]]["main"][1][0]["node"] == "Respond Error"

    validation_code = nodes["Validate API Result"]["parameters"]["jsCode"]
    assert "$('Prepare Lead').item.json" in validation_code
    assert "lead," in validation_code


def test_persistence_errors_are_sanitized_before_webhook_response() -> None:
    workflow = load_workflow()
    nodes = nodes_by_name(workflow)
    sanitizer = nodes["Sanitize Persistence Result"]
    sanitizer_code = sanitizer["parameters"]["jsCode"]
    response = nodes["Respond Error"]["parameters"]["responseBody"]

    assert "PERSISTENCE_ERROR" in sanitizer_code
    assert "statusCode: 503" in sanitizer_code
    assert "stack" not in sanitizer_code.lower()
    assert "connection" not in sanitizer_code.lower()
    assert response == "={{ { success: false, error: $json.error } }}"


def test_database_configuration_contains_no_committed_secret() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    workflow = load_workflow()
    postgres_credentials = nodes_by_name(workflow)["Persist Lead in PostgreSQL"][
        "credentials"
    ]["postgres"]

    password_line = next(
        line for line in env_example.splitlines() if line.startswith("POSTGRES_PASSWORD=")
    )
    assert password_line == "POSTGRES_PASSWORD="
    assert ".env" in gitignore
    assert set(postgres_credentials) == {"id", "name"}
    assert "password" not in json.dumps(postgres_credentials).lower()
