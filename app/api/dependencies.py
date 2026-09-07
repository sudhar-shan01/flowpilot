"""FastAPI dependencies shared by API routes."""

from functools import lru_cache

from app.core.config import get_settings
from app.services.ai_service import AIService, build_ai_service


@lru_cache
def get_ai_service() -> AIService:
    """Return the configured AI service instance."""
    return build_ai_service(get_settings())
