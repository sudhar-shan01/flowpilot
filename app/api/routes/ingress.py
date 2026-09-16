"""Internal reverse-proxy authorization for trusted lead sources."""

from __future__ import annotations

import hashlib
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from app.core.config import Settings, get_settings


router = APIRouter(prefix="/internal/ingress", include_in_schema=False)


def _digest(value: str) -> bytes:
    """Normalize arbitrary-length secrets before constant-time comparison."""
    return hashlib.sha256(value.encode("utf-8")).digest()


@router.get("/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_lead_webhook_secret(
    settings: Annotated[Settings, Depends(get_settings)],
    supplied_secret: Annotated[
        str | None,
        Header(alias="X-FlowPilot-Webhook-Secret"),
    ] = None,
) -> Response:
    """Authorize Caddy before it forwards the public lead webhook to n8n.

    An unset secret preserves local Phase 1-9 behavior. The production Compose
    layer requires a secret, so public lead ingress cannot silently run open.
    """
    configured = (
        settings.webhook_ingress_secret.get_secret_value()
        if settings.webhook_ingress_secret is not None
        else ""
    )
    if not configured:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    candidate = supplied_secret or ""
    if not secrets.compare_digest(_digest(candidate), _digest(configured)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook authentication failed.",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
