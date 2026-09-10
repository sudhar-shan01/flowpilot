"""Static, credential-free verification for the Phase 6 approval boundary."""

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
LEAD_WORKFLOW_PATH = ROOT / "n8n" / "flowpilot-lead-workflow.json"
APPROVAL_WORKFLOW_PATH = ROOT / "n8n" / "flowpilot-approval-workflow.json"
MIGRATION_PATH = ROOT / "postgres" / "init" / "003_add_human_approval.sql"


def load_workflow(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def nodes_by_name(workflow: dict[str, object]) -> dict[str, dict[str, object]]:
    nodes = workflow["nodes"]
    assert isinstance(nodes, list)
    return {node["name"]: node for node in nodes}


def first_target(workflow: dict[str, object], source: str, output: int = 0) -> str:
    return workflow["connections"][source]["main"][output][0]["node"]


def reachable(workflow: dict[str, object], source: str) -> set[str]:
    connections = workflow["connections"]
    seen: set[str] = set()
    pending = [source]
    while pending:
        current = pending.pop()
        for output in connections.get(current, {}).get("main", []):
            for target in output:
                name = target["node"]
                if name not in seen:
                    seen.add(name)
                    pending.append(name)
    return seen


def paths_to(
    workflow: dict[str, object], source: str, target: str
) -> list[list[str]]:
    connections = workflow["connections"]
    paths: list[list[str]] = []

    def visit(current: str, path: list[str]) -> None:
        if current == target:
            paths.append(path)
            return
        for output in connections.get(current, {}).get("main", []):
            for connection in output:
                name = connection["node"]
                if name not in path:
                    visit(name, [*path, name])

    visit(source, [source])
    return paths


def apply_guarded_decision(
    row: dict[str, object], *, token: str, decision: str, unexpired: bool = True
) -> bool:
    """Small state-model assertion companion to the static SQL checks."""
    if (
        row["status"] != "pending_approval"
        or row["token"] != token
        or not unexpired
        or decision not in {"approve", "reject"}
    ):
        return False
    row["status"] = "approved" if decision == "approve" else "rejected"
    row["token"] = None
    return True


def test_migration_is_additive_repeatable_and_preserves_existing_rows() -> None:
    migration = MIGRATION_PATH.read_text(encoding="utf-8").lower()

    for column in (
        "approval_token uuid",
        "approval_expires_at timestamptz",
        "approval_decided_at timestamptz",
        "initial_response_sent_at timestamptz",
    ):
        assert f"add column if not exists {column}" in migration
    assert "drop constraint if exists leads_draft_status_check" in migration
    assert "create unique index if not exists leads_approval_token_unique" in migration
    assert "where approval_token is not null" in migration
    for destructive in ("drop table", "truncate", "delete from", "update leads"):
        assert destructive not in migration


def test_migration_enforces_the_phase_6_state_machine() -> None:
    migration = MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "check (" in migration
    assert "draft_status in ('pending_approval', 'approved', 'rejected')" in migration
    assert "draft_status is null" in migration


def test_pending_draft_gets_a_unique_expiring_database_token_before_email() -> None:
    workflow = load_workflow(LEAD_WORKFLOW_PATH)
    nodes = nodes_by_name(workflow)
    token_node = nodes["Create Approval Token"]
    query = token_node["parameters"]["query"]

    assert token_node["type"] == "n8n-nodes-base.postgres"
    assert token_node["onError"] == "continueRegularOutput"
    assert "gen_random_uuid()" in query
    assert "INTERVAL '48 hours'" in query
    assert "draft_status = 'pending_approval'" in query
    assert token_node["parameters"]["options"]["queryReplacement"] == (
        "={{ [$json.lead_id] }}"
    )
    assert first_target(workflow, "Sanitize Draft Persistence") == (
        "Create Approval Token"
    )
    assert first_target(workflow, "Create Approval Token") == (
        "Sanitize Approval Token"
    )
    assert first_target(workflow, "Sanitize Approval Token") == (
        "Notify Only After Draft Persistence"
    )


def test_internal_notification_uses_configured_approve_and_reject_links() -> None:
    nodes = nodes_by_name(load_workflow(LEAD_WORKFLOW_PATH))
    prepare = nodes["Prepare Internal Notification"]["parameters"]["jsCode"]

    assert "$env.FLOWPILOT_APPROVAL_URL" in prepare
    assert "url.searchParams.set('lead_id', String(data.lead_id))" in prepare
    assert "url.searchParams.set('token', data.approval.token)" in prepare
    assert "url.searchParams.set('decision', decision)" in prepare
    assert "decisionUrl('approve')" in prepare
    assert "decisionUrl('reject')" in prepare
    assert "expires in 48 hours" in prepare
    assert "localhost" not in prepare
    assert "$env.FLOWPILOT_INTERNAL_EMAIL_FROM" in prepare


def test_get_approval_link_cannot_reach_atomic_decision() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    nodes = nodes_by_name(workflow)
    webhook = nodes["Approval Review Webhook"]

    assert webhook["parameters"]["path"] == "flowpilot/approval"
    assert webhook["parameters"]["httpMethod"] == "GET"
    assert "Atomically Authorize Decision" not in reachable(
        workflow, "Approval Review Webhook"
    )


def test_get_approval_link_cannot_reach_lead_email() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)

    assert "Send Persisted Draft to Lead" not in reachable(
        workflow, "Approval Review Webhook"
    )


def test_get_approval_link_cannot_mutate_postgres() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    nodes = nodes_by_name(workflow)
    get_nodes = {
        "Approval Review Webhook",
        *reachable(workflow, "Approval Review Webhook"),
    }

    assert not any(
        nodes[name]["type"] == "n8n-nodes-base.postgres" for name in get_nodes
    )
    assert not any(
        nodes[name]["type"] == "n8n-nodes-base.emailSend" for name in get_nodes
    )
    assert not any(
        nodes[name]["type"] == "n8n-nodes-base.httpRequest" for name in get_nodes
    )


def test_get_approval_link_cannot_consume_the_token() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    nodes = nodes_by_name(workflow)
    get_nodes = {
        "Approval Review Webhook",
        *reachable(workflow, "Approval Review Webhook"),
    }
    get_definition = json.dumps([nodes[name] for name in sorted(get_nodes)])

    assert "approval_token = NULL" not in get_definition
    assert "UPDATE leads" not in get_definition


def test_get_approval_link_cannot_change_draft_status() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    nodes = nodes_by_name(workflow)
    get_nodes = {
        "Approval Review Webhook",
        *reachable(workflow, "Approval Review Webhook"),
    }
    get_definition = json.dumps([nodes[name] for name in sorted(get_nodes)])

    assert "draft_status" not in get_definition


def test_get_approval_link_cannot_set_approval_decided_at() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    nodes = nodes_by_name(workflow)
    get_nodes = {
        "Approval Review Webhook",
        *reachable(workflow, "Approval Review Webhook"),
    }
    get_definition = json.dumps([nodes[name] for name in sorted(get_nodes)])

    assert "approval_decided_at" not in get_definition


def test_repeated_get_scanner_prefetch_has_no_side_effects() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    nodes = nodes_by_name(workflow)
    lead = {
        "draft_status": "pending_approval",
        "approval_token": "7cc78a01-2ce0-4f4f-88dc-ec577453f635",
        "approval_decided_at": None,
        "initial_response_sent_at": None,
    }
    before = lead.copy()

    for _ in range(3):
        get_nodes = {
            "Approval Review Webhook",
            *reachable(workflow, "Approval Review Webhook"),
        }
        assert not any(
            nodes[name]["type"]
            in {
                "n8n-nodes-base.postgres",
                "n8n-nodes-base.emailSend",
                "n8n-nodes-base.httpRequest",
            }
            for name in get_nodes
        )
        assert first_target(workflow, "Prepare Review Confirmation") == (
            "Respond Review Confirmation"
        )

    assert lead == before


def test_get_review_page_requires_an_explicit_post_and_hides_the_token() -> None:
    nodes = nodes_by_name(load_workflow(APPROVAL_WORKFLOW_PATH))
    prepare = nodes["Prepare Review Confirmation"]["parameters"]["jsCode"]
    response = nodes["Respond Review Confirmation"]["parameters"]

    assert "<form method='post' action='?'" in prepare
    assert "type='hidden' name='lead_id'" in prepare
    assert "type='hidden' name='token'" in prepare
    assert "type='hidden' name='decision'" in prepare
    assert "No change has been made yet" in prepare
    assert "${data.token}" in prepare
    assert "token" not in prepare.split("<main>", 1)[1].split("<form", 1)[0].lower()
    headers = response["options"]["responseHeaders"]["entries"]
    assert {header["name"]: header["value"] for header in headers}["Content-Type"] == (
        "text/html; charset=utf-8"
    )


def test_get_review_validates_only_basic_authorization_fields() -> None:
    nodes = nodes_by_name(load_workflow(APPROVAL_WORKFLOW_PATH))
    validator = nodes["Validate Review Request"]["parameters"]["jsCode"]

    assert "$json.query" in validator
    assert "new Set(['lead_id', 'token', 'decision'])" in validator
    assert "Object.keys(query).some" in validator
    assert "query.lead_id === 'string'" in validator
    assert "query.token === 'string'" in validator
    assert "uuidPattern.test(token)" in validator
    assert "['approve', 'reject'].includes(decision)" in validator
    for forbidden in ("subject", "recipient", "company", "draft_body"):
        assert forbidden not in validator


def test_only_post_can_reach_the_atomic_decision_node() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    nodes = nodes_by_name(workflow)
    webhooks = [
        name
        for name, node in nodes.items()
        if node["type"] == "n8n-nodes-base.webhook"
    ]

    assert webhooks == ["Approval Review Webhook", "Approval Decision Webhook"]
    assert "Atomically Authorize Decision" not in reachable(
        workflow, "Approval Review Webhook"
    )
    assert "Atomically Authorize Decision" in reachable(
        workflow, "Approval Decision Webhook"
    )
    assert nodes["Approval Decision Webhook"]["parameters"]["httpMethod"] == "POST"


def test_post_approval_webhook_accepts_only_the_authorization_fields() -> None:
    nodes = nodes_by_name(load_workflow(APPROVAL_WORKFLOW_PATH))
    webhook = nodes["Approval Decision Webhook"]
    validator = nodes["Validate Approval Request"]["parameters"]["jsCode"]

    assert webhook["parameters"]["path"] == "flowpilot/approval"
    assert webhook["parameters"]["httpMethod"] == "POST"
    assert webhook["parameters"]["responseMode"] == "responseNode"
    assert "new Set(['lead_id', 'token', 'decision'])" in validator
    assert "Object.keys(body).some" in validator
    assert "Object.keys(query).length > 0" in validator
    assert "$json.body" in validator
    assert "$json.query" in validator
    for forbidden in ("subject", "recipient", "company", "draft_body"):
        assert forbidden not in validator


def test_request_validation_covers_missing_blank_and_malformed_values() -> None:
    validator = nodes_by_name(load_workflow(APPROVAL_WORKFLOW_PATH))[
        "Validate Approval Request"
    ]["parameters"]["jsCode"]

    assert "body.lead_id === 'string'" in validator
    assert "body.token === 'string'" in validator
    assert ".trim()" in validator
    assert "/^[1-9]\\d*$/" in validator
    assert "uuidPattern.test(token)" in validator
    assert "['approve', 'reject'].includes(decision)" in validator
    assert "Number.isSafeInteger(leadId)" in validator


def test_post_approve_reaches_email_only_after_atomic_authorization() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    paths = paths_to(
        workflow, "Approval Decision Webhook", "Send Persisted Draft to Lead"
    )

    assert paths
    for path in paths:
        assert path.index("Atomically Authorize Decision") < path.index(
            "Send Persisted Draft to Lead"
        )
        assert path.index("Route Approval Result") < path.index(
            "Send Persisted Draft to Lead"
        )
    assert first_target(workflow, "Route Approval Result", 0) == (
        "Prepare Approved Email"
    )


def test_decision_transition_is_atomic_parameterized_and_single_use() -> None:
    nodes = nodes_by_name(load_workflow(APPROVAL_WORKFLOW_PATH))
    transition = nodes["Atomically Authorize Decision"]
    query = transition["parameters"]["query"]
    replacements = transition["parameters"]["options"]["queryReplacement"]

    assert "UPDATE leads" in query
    assert "WHERE id = $1" in query
    assert "approval_token = $2::uuid" in query
    assert "draft_status = 'pending_approval'" in query
    assert "approval_expires_at > NOW()" in query
    assert "$3::text IN ('approve', 'reject')" in query
    assert "approval_token = NULL" in query
    assert "approval_decided_at = NOW()" in query
    assert replacements == "={{ [$json.leadId, $json.token, $json.decision] }}"
    assert "$json" not in query
    assert transition["onError"] == "continueRegularOutput"


def test_wrong_expired_used_cross_lead_and_legacy_tokens_cannot_transition() -> None:
    query = nodes_by_name(load_workflow(APPROVAL_WORKFLOW_PATH))[
        "Atomically Authorize Decision"
    ]["parameters"]["query"]

    # Lead ID and token must match the same pending, unexpired row. A cleared or
    # legacy NULL token, an expired token, or either link after the first click
    # therefore updates no row and receives the same generic response.
    assert "id = $1" in query
    assert "approval_token = $2::uuid" in query
    assert "draft_status = 'pending_approval'" in query
    assert "approval_expires_at > NOW()" in query
    assert "EXISTS (SELECT 1 FROM transitioned) AS authorized" in query
    invalid = nodes_by_name(load_workflow(APPROVAL_WORKFLOW_PATH))[
        "Respond Invalid Approval"
    ]["parameters"]["responseBody"]
    assert invalid == (
        "FlowPilot — This approval link is invalid, expired, or has already been used."
    )


def test_double_approve_and_approve_then_reject_are_single_use() -> None:
    row: dict[str, object] = {"status": "pending_approval", "token": "token-a"}

    assert apply_guarded_decision(row, token="token-a", decision="approve")
    assert row == {"status": "approved", "token": None}
    assert not apply_guarded_decision(row, token="token-a", decision="approve")
    assert not apply_guarded_decision(row, token="token-a", decision="reject")


def test_double_reject_and_reject_then_approve_are_single_use() -> None:
    row: dict[str, object] = {"status": "pending_approval", "token": "token-b"}

    assert apply_guarded_decision(row, token="token-b", decision="reject")
    assert row == {"status": "rejected", "token": None}
    assert not apply_guarded_decision(row, token="token-b", decision="reject")
    assert not apply_guarded_decision(row, token="token-b", decision="approve")


def test_wrong_expired_missing_and_legacy_tokens_do_not_change_state() -> None:
    for stored_token, presented_token, unexpired in (
        ("right", "wrong", True),
        ("right", "right", False),
        (None, "right", True),
        ("right", "", True),
    ):
        row: dict[str, object] = {
            "status": "pending_approval",
            "token": stored_token,
        }
        assert not apply_guarded_decision(
            row,
            token=presented_token,
            decision="approve",
            unexpired=unexpired,
        )
        assert row == {"status": "pending_approval", "token": stored_token}


def test_approve_requires_stored_recipient_and_draft_but_reject_does_not() -> None:
    query = nodes_by_name(load_workflow(APPROVAL_WORKFLOW_PATH))[
        "Atomically Authorize Decision"
    ]["parameters"]["query"]

    assert "$3::text = 'reject'" in query
    assert "NULLIF(BTRIM(email), '') IS NOT NULL" in query
    assert "NULLIF(BTRIM(draft_subject), '') IS NOT NULL" in query
    assert "NULLIF(BTRIM(draft_body), '') IS NOT NULL" in query


def test_approved_email_uses_only_values_returned_by_postgres() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    nodes = nodes_by_name(workflow)
    sanitizer = nodes["Sanitize Authorization Result"]["parameters"]["jsCode"]
    email = nodes["Send Persisted Draft to Lead"]

    assert "recipient: row.email" in sanitizer
    assert "subject: row.draft_subject" in sanitizer
    assert "body: row.draft_body" in sanitizer
    assert email["parameters"]["toEmail"] == "={{ $json.email.recipient }}"
    assert email["parameters"]["subject"] == "={{ $json.email.subject }}"
    assert email["parameters"]["text"] == "={{ $json.email.body }}"
    assert email["parameters"]["emailFormat"] == "text"
    serialized = json.dumps(email["parameters"])
    for untrusted in ("query", "token", "decision", "lead_id"):
        assert untrusted not in serialized


def test_approval_workflow_never_calls_ai_or_regenerates_the_draft() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)

    assert not any(node["type"] == "n8n-nodes-base.httpRequest" for node in workflow["nodes"])
    workflow_text = APPROVAL_WORKFLOW_PATH.read_text(encoding="utf-8").lower()
    for forbidden in ("/lead/draft", "/lead/analyze", "openai", "ai provider"):
        assert forbidden not in workflow_text


def test_reject_and_invalid_paths_can_never_reach_the_email_node() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    email_node = "Send Persisted Draft to Lead"

    assert first_target(workflow, "Route Approval Result", 1) == "Respond Rejected"
    assert first_target(workflow, "Authorize Only Valid Request", 1) == (
        "Respond Invalid Approval"
    )
    assert email_node not in reachable(workflow, "Respond Rejected")
    assert email_node not in reachable(workflow, "Respond Invalid Approval")
    assert first_target(workflow, "Route Approval Result", 0) == (
        "Prepare Approved Email"
    )


def test_sender_is_validated_before_the_email_node() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    nodes = nodes_by_name(workflow)
    prepare = nodes["Prepare Approved Email"]["parameters"]["jsCode"]

    assert "$env.FLOWPILOT_LEAD_EMAIL_FROM" in prepare
    assert "addressPattern.test(sender)" in prepare
    assert "addressPattern.test(recipient)" in prepare
    assert first_target(workflow, "Prepare Approved Email") == (
        "Send Only Configured Lead Email"
    )
    assert first_target(workflow, "Send Only Configured Lead Email", 1) == (
        "Respond Email Failure"
    )


def test_smtp_failure_stays_approved_and_does_not_record_sent_timestamp() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    nodes = nodes_by_name(workflow)

    assert nodes["Send Persisted Draft to Lead"]["onError"] == (
        "continueRegularOutput"
    )
    assert first_target(workflow, "Record Only Sent Email", 0) == (
        "Record Response Sent Timestamp"
    )
    assert first_target(workflow, "Record Only Sent Email", 1) == (
        "Respond Email Failure"
    )
    all_queries = "\n".join(
        node["parameters"].get("query", "") for node in workflow["nodes"]
    )
    assert "SET initial_response_sent_at = NOW()" in all_queries
    assert "SET draft_status = 'pending_approval'" not in all_queries
    assert "initial_response_sent_at IS NULL" in all_queries


def test_browser_responses_are_static_sanitized_and_truthful() -> None:
    nodes = nodes_by_name(load_workflow(APPROVAL_WORKFLOW_PATH))
    expected = {
        "Respond Approved": "FlowPilot — Draft approved and response sent.",
        "Respond Rejected": "FlowPilot — Draft rejected. No email was sent.",
        "Respond Approval Error": "FlowPilot — This approval request could not be completed.",
        "Respond Email Failure": "FlowPilot — Draft approved, but the response email could not be sent.",
    }
    for name, body in expected.items():
        response = nodes[name]["parameters"]
        assert response["respondWith"] == "text"
        assert response["responseBody"] == body
        for forbidden in ("token", "smtp", "database", "lead_id", "recipient"):
            assert forbidden not in body.lower()


def test_credential_exports_contain_references_but_no_secrets() -> None:
    workflow = load_workflow(APPROVAL_WORKFLOW_PATH)
    nodes = nodes_by_name(workflow)

    for node_name, credential_type, expected_name in (
        ("Atomically Authorize Decision", "postgres", "FlowPilot PostgreSQL"),
        ("Record Response Sent Timestamp", "postgres", "FlowPilot PostgreSQL"),
        ("Send Persisted Draft to Lead", "smtp", "FlowPilot Email"),
    ):
        reference = nodes[node_name]["credentials"][credential_type]
        assert set(reference) == {"id", "name"}
        assert reference["name"] == expected_name

    text = APPROVAL_WORKFLOW_PATH.read_text(encoding="utf-8").lower()
    for secret_key in ("smtp_password", "postgres_password", "oauth_token", "app_password"):
        assert secret_key not in text


def test_phase_6_environment_variables_are_documented_without_values() -> None:
    env_lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()

    assert "FLOWPILOT_APPROVAL_URL=" in env_lines
    assert "FLOWPILOT_LEAD_EMAIL_FROM=" in env_lines
    assert (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()[0] == ".env"
