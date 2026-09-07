"""Deterministic FlowPilot app used for local n8n end-to-end verification."""

from fastapi import FastAPI

from app.api.dependencies import get_ai_service
from app.main import app
from app.models.lead import LeadRequest
from app.services.ai_service import AIProvider, AIService


class BudgetRoutingProvider(AIProvider):
    """Return predictable priorities while preserving the production API path."""

    async def analyze_lead(self, lead: LeadRequest) -> object:
        if lead.company == "Malformed Response Test":
            return {"unexpected": "response"}

        if lead.budget >= 5_000:
            priority = "high"
            score = 88
        elif lead.budget >= 1_000:
            priority = "medium"
            score = 60
        else:
            priority = "low"
            score = 30

        return {
            "category": "workflow automation",
            "priority": priority,
            "lead_score": score,
            "short_summary": f"{lead.company} needs workflow automation.",
            "recommended_action": "Review the lead and follow the routed action.",
        }


app.dependency_overrides[get_ai_service] = lambda: AIService(BudgetRoutingProvider())


malformed_app = FastAPI()


@malformed_app.post("/lead/analyze")
async def malformed_analysis() -> dict[str, str]:
    """Return HTTP 200 with a deliberately invalid analysis for n8n testing."""
    return {"unexpected": "response"}
