"""Provider-independent AI lead analysis service."""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.models.lead import LeadAnalysis, LeadDraft, LeadDraftRequest, LeadRequest

logger = logging.getLogger(__name__)


class AIServiceError(Exception):
    """Base class for expected AI analysis failures."""


class AIConfigurationError(AIServiceError):
    """Raised when the configured provider cannot be used."""


class AIProviderUnavailableError(AIServiceError):
    """Raised when the provider fails or times out."""


class AIResponseError(AIServiceError):
    """Raised when provider output does not match the response schema."""


class AIProvider(ABC):
    """Small interface implemented by any supported LLM provider."""

    @abstractmethod
    async def analyze_lead(self, lead: LeadRequest) -> object:
        """Return untrusted structured data describing the lead."""
        raise NotImplementedError

    async def draft_lead_response(self, request: LeadDraftRequest) -> object:
        """Return an untrusted structured response draft."""
        del request
        raise NotImplementedError


class UnconfiguredAIProvider(AIProvider):
    """Keeps application startup and health checks independent of AI credentials."""

    async def analyze_lead(self, lead: LeadRequest) -> object:
        del lead
        raise AIConfigurationError("An AI provider API key is required")

    async def draft_lead_response(self, request: LeadDraftRequest) -> object:
        del request
        raise AIConfigurationError("An AI provider API key is required")


class OpenAIProvider(AIProvider):
    """OpenAI-compatible chat-completions provider using JSON Schema output."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def analyze_lead(self, lead: LeadRequest) -> object:
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You analyze inbound business leads. Return only the requested "
                        "structured result. Base the score and action on stated need, "
                        "business fit, urgency, specificity, and budget. Do not invent facts."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(lead.model_dump(mode="json")),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "lead_analysis",
                    "strict": True,
                    "schema": LeadAnalysis.model_json_schema(),
                },
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise AIProviderUnavailableError("AI provider timed out") from exc
        except httpx.RequestError as exc:
            raise AIProviderUnavailableError("AI provider request failed") from exc

        if response.status_code in {401, 403}:
            raise AIConfigurationError("AI provider rejected its credentials")
        if not response.is_success:
            raise AIProviderUnavailableError("AI provider returned an error")

        try:
            body: dict[str, Any] = response.json()
            content = body["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AIResponseError("AI provider returned malformed output") from exc

    async def draft_lead_response(self, request: LeadDraftRequest) -> object:
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Write a concise, professional plain-text response draft for an "
                        "inbound business lead. Use only the supplied lead and analysis. "
                        "Do not invent pricing, discounts, timelines, promises, capabilities, "
                        "recipient addresses, names, or signatures. Return only the requested "
                        "structured subject and body."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(request.model_dump(mode="json")),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "lead_response_draft",
                    "strict": True,
                    "schema": LeadDraft.model_json_schema(),
                },
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise AIProviderUnavailableError("AI provider timed out") from exc
        except httpx.RequestError as exc:
            raise AIProviderUnavailableError("AI provider request failed") from exc

        if response.status_code in {401, 403}:
            raise AIConfigurationError("AI provider rejected its credentials")
        if not response.is_success:
            raise AIProviderUnavailableError("AI provider returned an error")

        try:
            body: dict[str, Any] = response.json()
            content = body["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AIResponseError("AI provider returned malformed output") from exc


class AIService:
    """Coordinates provider calls and validates all untrusted model output."""

    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    async def analyze_lead(self, lead: LeadRequest) -> LeadAnalysis:
        try:
            raw_result = await self._provider.analyze_lead(lead)
        except AIServiceError:
            raise
        except Exception as exc:
            logger.exception("Unexpected AI provider failure")
            raise AIProviderUnavailableError("Unexpected AI provider failure") from exc

        try:
            return LeadAnalysis.model_validate(raw_result)
        except ValidationError as exc:
            raise AIResponseError("AI response failed schema validation") from exc

    async def draft_lead_response(self, request: LeadDraftRequest) -> LeadDraft:
        try:
            raw_result = await self._provider.draft_lead_response(request)
        except AIServiceError:
            raise
        except Exception as exc:
            logger.exception("Unexpected AI provider failure")
            raise AIProviderUnavailableError("Unexpected AI provider failure") from exc

        try:
            return LeadDraft.model_validate(raw_result)
        except ValidationError as exc:
            raise AIResponseError("AI draft failed schema validation") from exc


def build_ai_service(settings: Settings) -> AIService:
    """Construct the configured provider behind the stable service interface."""
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
        return AIService(UnconfiguredAIProvider())

    provider = OpenAIProvider(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
        base_url=settings.openai_base_url,
        timeout_seconds=settings.ai_timeout_seconds,
    )
    return AIService(provider)
