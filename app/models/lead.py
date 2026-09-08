"""Pydantic models for lead intake, analysis, and response drafts."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class StrictAPIModel(BaseModel):
    """Reject unknown fields so client mistakes are visible immediately."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HealthResponse(StrictAPIModel):
    """Health endpoint response."""

    status: Literal["healthy"]


class LeadRequest(StrictAPIModel):
    """Validated business lead submitted for analysis."""

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    company: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=10, max_length=5_000)
    budget: float = Field(ge=0, le=1_000_000_000, allow_inf_nan=False)

    @field_validator("name", "company", "message")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """Reject values that only contained whitespace before stripping."""
        if not value:
            raise ValueError("must not be blank")
        return value


class LeadAnalysis(StrictAPIModel):
    """Structured AI analysis returned to the API caller."""

    category: str = Field(min_length=1, max_length=80)
    priority: Literal["low", "medium", "high"]
    lead_score: int = Field(ge=0, le=100)
    short_summary: str = Field(min_length=1, max_length=500)
    recommended_action: str = Field(min_length=1, max_length=500)


class LeadDraftRequest(StrictAPIModel):
    """Validated lead and analysis context used to generate a response draft."""

    lead: LeadRequest
    analysis: LeadAnalysis


class LeadDraft(StrictAPIModel):
    """Plain-text response draft awaiting human approval."""

    subject: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=2_000)

    @field_validator("subject")
    @classmethod
    def subject_must_be_one_line(cls, value: str) -> str:
        """Keep the subject safe for a plain-text mail header."""
        if "\n" in value or "\r" in value:
            raise ValueError("subject must be a single line")
        return value

    @field_validator("subject", "body")
    @classmethod
    def reject_html(cls, value: str) -> str:
        """Reject HTML-like output; Phase 5 drafts are plain text only."""
        if re.search(r"<\s*/?\s*[a-z][^>]*>", value, flags=re.IGNORECASE):
            raise ValueError("HTML is not allowed")
        return value
