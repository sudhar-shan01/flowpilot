"""Sanitized operator-facing reliability aggregates."""

from pydantic import BaseModel, Field


class ReconciliationQueueCounts(BaseModel):
    """Aggregate counts only; no customer or credential data is returned."""

    initial_response_uncertain: int = Field(ge=0)
    followup_uncertain: int = Field(ge=0)
    recovery_required: int = Field(ge=0)
    stale_sending: int = Field(ge=0)
    stale_claimed: int = Field(ge=0)
