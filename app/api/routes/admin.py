"""Opt-in, internal-only operational observability routes."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_reconciliation_repository
from app.core.config import Settings, get_settings
from app.models.reconciliation import ReconciliationQueueCounts
from app.services.reconciliation import ReconciliationRepository


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", include_in_schema=False)


@router.get("/reconciliation-queue", response_model=ReconciliationQueueCounts)
def get_reconciliation_queue_counts(
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[
        ReconciliationRepository, Depends(get_reconciliation_repository)
    ],
) -> ReconciliationQueueCounts:
    """Return privacy-safe aggregate counts when explicitly enabled."""
    if not settings.admin_api_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    try:
        counts = repository.fetch_counts()
        return ReconciliationQueueCounts.model_validate(counts)
    except Exception as exc:
        logger.error("Reconciliation aggregate query failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reconciliation data is temporarily unavailable.",
        ) from exc
