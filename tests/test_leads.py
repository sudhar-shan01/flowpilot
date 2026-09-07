"""Lead endpoint validation and failure-path tests."""

from fastapi.testclient import TestClient

from app.api.dependencies import get_ai_service
from app.main import app
from app.models.lead import LeadRequest
from app.services.ai_service import AIProvider, AIService, UnconfiguredAIProvider


def test_analyze_valid_lead(
    client: TestClient,
    valid_lead: dict[str, object],
) -> None:
    response = client.post("/lead/analyze", json=valid_lead)

    assert response.status_code == 200
    assert response.json() == {
        "category": "workflow automation",
        "priority": "high",
        "lead_score": 88,
        "short_summary": "Acme needs inventory automation for a 25-person team.",
        "recommended_action": "Schedule a discovery call to map inventory workflows.",
    }


def test_rejects_invalid_email(
    client: TestClient,
    valid_lead: dict[str, object],
) -> None:
    valid_lead["email"] = "not-an-email"

    response = client.post("/lead/analyze", json=valid_lead)

    assert response.status_code == 422


def test_rejects_negative_budget(
    client: TestClient,
    valid_lead: dict[str, object],
) -> None:
    valid_lead["budget"] = -1

    response = client.post("/lead/analyze", json=valid_lead)

    assert response.status_code == 422


def test_rejects_missing_required_fields(client: TestClient) -> None:
    response = client.post("/lead/analyze", json={"name": "John Doe"})

    assert response.status_code == 422


class FailingProvider(AIProvider):
    async def analyze_lead(self, lead: LeadRequest) -> object:
        del lead
        raise RuntimeError("simulated provider outage")


def test_ai_service_failure(
    client: TestClient,
    valid_lead: dict[str, object],
) -> None:
    app.dependency_overrides[get_ai_service] = lambda: AIService(FailingProvider())

    response = client.post("/lead/analyze", json=valid_lead)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "AI analysis service is temporarily unavailable."
    }


def test_missing_api_key_is_handled(
    client: TestClient,
    valid_lead: dict[str, object],
) -> None:
    app.dependency_overrides[get_ai_service] = lambda: AIService(
        UnconfiguredAIProvider()
    )

    response = client.post("/lead/analyze", json=valid_lead)

    assert response.status_code == 503
    assert response.json() == {"detail": "AI analysis service is not configured."}


class MalformedProvider(AIProvider):
    async def analyze_lead(self, lead: LeadRequest) -> object:
        del lead
        return {"category": "workflow automation", "lead_score": 999}


def test_malformed_ai_response(
    client: TestClient,
    valid_lead: dict[str, object],
) -> None:
    app.dependency_overrides[get_ai_service] = lambda: AIService(MalformedProvider())

    response = client.post("/lead/analyze", json=valid_lead)

    assert response.status_code == 502
    assert response.json() == {
        "detail": "AI analysis service returned an invalid response."
    }
