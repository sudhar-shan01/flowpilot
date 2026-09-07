"""Service health endpoint."""

from fastapi import APIRouter

from app.models.lead import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Report whether the API process is available."""
    return HealthResponse(status="healthy")
