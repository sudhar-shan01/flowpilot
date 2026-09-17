"""Focused tests for the Phase 10 trusted lead-ingress boundary."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.routes import ingress
from app.core.config import Settings, get_settings
from app.main import app


def configure_secret(value: str | None) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        webhook_ingress_secret=value
    )


def test_ingress_secret_configuration_allows_unset_value() -> None:
    settings = Settings(webhook_ingress_secret=None)

    assert settings.webhook_ingress_secret is None


def test_ingress_secret_configuration_accepts_32_characters() -> None:
    secret = "a" * 32

    settings = Settings(webhook_ingress_secret=secret)

    assert settings.webhook_ingress_secret is not None
    assert settings.webhook_ingress_secret.get_secret_value() == secret


def test_ingress_secret_configuration_rejects_short_value() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(webhook_ingress_secret="a" * 31)

    assert error.value.errors()[0]["type"] == "too_short"


def test_local_ingress_remains_compatible_when_secret_is_unset(
    client: TestClient,
) -> None:
    configure_secret(None)

    response = client.get("/internal/ingress/verify")

    assert response.status_code == 204
    assert response.content == b""


def test_configured_ingress_rejects_missing_and_wrong_secrets(
    client: TestClient,
) -> None:
    configure_secret("configured-production-secret-at-least-32-bytes")

    missing = client.get("/internal/ingress/verify")
    wrong = client.get(
        "/internal/ingress/verify",
        headers={"X-FlowPilot-Webhook-Secret": "wrong-secret"},
    )

    for response in (missing, wrong):
        assert response.status_code == 401
        assert response.json() == {"detail": "Webhook authentication failed."}
        assert "configured-production-secret" not in response.text
        assert "wrong-secret" not in response.text


def test_configured_ingress_accepts_exact_secret(client: TestClient) -> None:
    secret = "configured-production-secret-at-least-32-bytes"
    configure_secret(secret)

    response = client.get(
        "/internal/ingress/verify",
        headers={"X-FlowPilot-Webhook-Secret": secret},
    )

    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.parametrize("supplied", ["", " ", "malformed", "x" * 31])
def test_configured_ingress_rejects_malformed_secret_values(
    client: TestClient, supplied: str
) -> None:
    configure_secret("configured-production-secret-at-least-32-bytes")

    response = client.get(
        "/internal/ingress/verify",
        headers={"X-FlowPilot-Webhook-Secret": supplied},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Webhook authentication failed."}


def test_ingress_secret_comparison_is_case_sensitive(client: TestClient) -> None:
    secret = "Case-Sensitive-Production-Secret-32-Bytes"
    configure_secret(secret)

    response = client.get(
        "/internal/ingress/verify",
        headers={"X-FlowPilot-Webhook-Secret": secret.lower()},
    )

    assert response.status_code == 401


def test_ingress_uses_constant_time_digest_comparison(
    client: TestClient,
    monkeypatch,
) -> None:
    configure_secret("configured-production-secret-at-least-32-bytes")
    compared: list[tuple[bytes, bytes]] = []

    def record_compare(left: bytes, right: bytes) -> bool:
        compared.append((left, right))
        return True

    monkeypatch.setattr(ingress.secrets, "compare_digest", record_compare)

    response = client.get(
        "/internal/ingress/verify",
        headers={"X-FlowPilot-Webhook-Secret": "candidate"},
    )

    assert response.status_code == 204
    assert len(compared) == 1
    assert len(compared[0][0]) == len(compared[0][1]) == 32
    assert b"candidate" not in compared[0]


def test_ingress_secret_never_appears_in_application_logs(
    client: TestClient,
    caplog,
) -> None:
    configured = "configured-production-secret-at-least-32-bytes"
    supplied = "attacker-supplied-secret"
    configure_secret(configured)

    with caplog.at_level(logging.DEBUG):
        response = client.get(
            "/internal/ingress/verify",
            headers={"X-FlowPilot-Webhook-Secret": supplied},
        )

    assert response.status_code == 401
    assert configured not in caplog.text
    assert supplied not in caplog.text


def test_internal_verifier_is_not_published_in_openapi(client: TestClient) -> None:
    assert "/internal/ingress/verify" not in client.get("/openapi.json").json()[
        "paths"
    ]
