"""Network-free tests for AI-generated lead response drafts."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_ai_service
from app.main import app
from app.models.lead import LeadDraftRequest, LeadRequest
from app.services.ai_service import AIProvider, AIService, UnconfiguredAIProvider


class DraftProvider(AIProvider):
    def __init__(self, result: object) -> None:
        self.result = result

    async def analyze_lead(self, lead: LeadRequest) -> object:
        del lead
        raise AssertionError("analysis must not run during draft generation")

    async def draft_lead_response(self, request: LeadDraftRequest) -> object:
        assert request.lead.email == "john@example.com"
        assert request.analysis.priority == "high"
        return self.result


class FailingDraftProvider(DraftProvider):
    async def draft_lead_response(self, request: LeadDraftRequest) -> object:
        del request
        raise RuntimeError("provider detail that must remain private")


@pytest.fixture
def draft_request(valid_lead: dict[str, object]) -> dict[str, Any]:
    return {
        "lead": valid_lead,
        "analysis": {
            "category": "workflow automation",
            "priority": "high",
            "lead_score": 88,
            "short_summary": "Acme needs inventory automation.",
            "recommended_action": "Schedule a discovery call.",
        },
    }


def override_provider(provider: AIProvider) -> None:
    app.dependency_overrides[get_ai_service] = lambda: AIService(provider)


def test_draft_endpoint_returns_structured_plain_text(
    client: TestClient,
    draft_request: dict[str, Any],
) -> None:
    override_provider(
        DraftProvider(
            {
                "subject": "Next steps for Acme",
                "body": "Thank you for sharing your inventory automation needs.\n\n"
                "We would be glad to discuss the workflow with you.",
            }
        )
    )

    response = client.post("/lead/draft", json=draft_request)

    assert response.status_code == 200
    assert response.json() == {
        "subject": "Next steps for Acme",
        "body": "Thank you for sharing your inventory automation needs.\n\n"
        "We would be glad to discuss the workflow with you.",
    }


def test_draft_endpoint_validates_input(client: TestClient) -> None:
    response = client.post("/lead/draft", json={"lead": {"name": "A"}})

    assert response.status_code == 422


@pytest.mark.parametrize(
    "draft",
    [
        pytest.param({"subject": " ", "body": "Useful body"}, id="blank-subject"),
        pytest.param({"subject": "Useful subject", "body": " \t "}, id="blank-body"),
    ],
)
def test_draft_endpoint_rejects_whitespace_only_output(
    client: TestClient,
    draft_request: dict[str, Any],
    draft: dict[str, object],
) -> None:
    override_provider(DraftProvider(draft))

    response = client.post("/lead/draft", json=draft_request)

    assert response.status_code == 502
    assert response.json() == {
        "detail": "AI draft service returned an invalid response."
    }


@pytest.mark.parametrize(
    "draft",
    [
        {"subject": "", "body": "Useful body"},
        {"subject": "Useful subject", "body": ""},
        {"subject": "Useful subject", "body": "<script>alert('x')</script>"},
        {"subject": "Useful subject", "body": "x" * 2_001},
        {"subject": "First line\nInjected header", "body": "Useful body"},
        {"subject": "Missing body"},
    ],
)
def test_draft_endpoint_rejects_malformed_or_unsafe_output(
    client: TestClient,
    draft_request: dict[str, Any],
    draft: dict[str, object],
) -> None:
    override_provider(DraftProvider(draft))

    response = client.post("/lead/draft", json=draft_request)

    assert response.status_code == 502
    assert response.json() == {
        "detail": "AI draft service returned an invalid response."
    }


def test_draft_provider_failure_is_sanitized(
    client: TestClient,
    draft_request: dict[str, Any],
) -> None:
    override_provider(FailingDraftProvider({}))

    response = client.post("/lead/draft", json=draft_request)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "AI draft service is temporarily unavailable."
    }
    assert "provider detail" not in response.text


def test_draft_missing_api_key_is_sanitized(
    client: TestClient,
    draft_request: dict[str, Any],
) -> None:
    override_provider(UnconfiguredAIProvider())

    response = client.post("/lead/draft", json=draft_request)

    assert response.status_code == 503
    assert response.json() == {"detail": "AI draft service is not configured."}
