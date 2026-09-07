"""Lead analysis endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_ai_service
from app.models.lead import LeadAnalysis, LeadRequest
from app.services.ai_service import (
    AIConfigurationError,
    AIProviderUnavailableError,
    AIResponseError,
    AIService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lead", tags=["leads"])


@router.post("/analyze", response_model=LeadAnalysis)
async def analyze_lead(
    lead: LeadRequest,
    ai_service: Annotated[AIService, Depends(get_ai_service)],
) -> LeadAnalysis:
    """Validate and analyze a business lead."""
    try:
        result = await ai_service.analyze_lead(lead)
    except AIConfigurationError as exc:
        logger.warning("Lead analysis requested while the AI provider is unconfigured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI analysis service is not configured.",
        ) from exc
    except AIProviderUnavailableError as exc:
        logger.warning("AI provider was unavailable during lead analysis")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI analysis service is temporarily unavailable.",
        ) from exc
    except AIResponseError as exc:
        logger.error("AI provider returned an invalid structured response")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI analysis service returned an invalid response.",
        ) from exc

    logger.info(
        "Lead analysis completed category=%s priority=%s score=%s",
        result.category,
        result.priority,
        result.lead_score,
    )
    return result
