"""Shared test fixtures."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_ai_service
from app.main import app
from app.models.lead import LeadRequest
from app.services.ai_service import AIProvider, AIService


class SuccessfulProvider(AIProvider):
    """Deterministic provider used by API tests."""

    async def analyze_lead(self, lead: LeadRequest) -> object:
        assert lead.company == "Acme Industries"
        return {
            "category": "workflow automation",
            "priority": "high",
            "lead_score": 88,
            "short_summary": "Acme needs inventory automation for a 25-person team.",
            "recommended_action": "Schedule a discovery call to map inventory workflows.",
        }


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Create a client with network-free AI behavior."""
    app.dependency_overrides[get_ai_service] = lambda: AIService(SuccessfulProvider())
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def valid_lead() -> dict[str, object]:
    return {
        "name": "John Doe",
        "email": "john@example.com",
        "company": "Acme Industries",
        "message": "We need an automated inventory management solution for our 25-person company.",
        "budget": 5000,
    }
