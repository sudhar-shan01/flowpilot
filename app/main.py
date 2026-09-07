"""FastAPI application entry point."""

import logging

from fastapi import FastAPI

from app.api.routes import health, leads
from app.core.config import get_settings


def create_app() -> FastAPI:
    """Create and configure the FlowPilot API."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    application = FastAPI(
        title=settings.app_name,
        description="AI-powered business lead analysis API.",
        version="0.1.0",
    )
    application.include_router(health.router)
    application.include_router(leads.router)
    return application


app = create_app()
