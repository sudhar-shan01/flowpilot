"""API contract tests for opt-in reconciliation observability."""

from collections.abc import Mapping

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_reconciliation_repository
from app.core.config import Settings, get_settings
from app.main import app


COUNTS = {
    "initial_response_uncertain": 2,
    "followup_uncertain": 3,
    "recovery_required": 4,
    "stale_sending": 5,
    "stale_claimed": 6,
}


class CountingRepository:
    def __init__(self, counts: Mapping[str, int] = COUNTS) -> None:
        self.counts = counts
        self.calls = 0

    def fetch_counts(self) -> Mapping[str, int]:
        self.calls += 1
        return self.counts


def request_with_setting(value: str | None):
    settings = Settings(_env_file=None, admin_enabled=value or "false")
    repository = CountingRepository()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_reconciliation_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            response = client.get("/admin/reconciliation-queue")
    finally:
        app.dependency_overrides.clear()
    return response, repository


@pytest.mark.parametrize("value", [None, "false", "FALSE", "invalid", "1", "yes"])
def test_admin_endpoint_is_unavailable_unless_explicitly_enabled(value) -> None:
    response, repository = request_with_setting(value)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert repository.calls == 0


def test_admin_endpoint_returns_only_stable_aggregate_counts() -> None:
    response, repository = request_with_setting("true")
    assert response.status_code == 200
    assert response.json() == COUNTS
    assert repository.calls == 1
    serialized = response.text.lower()
    for forbidden in (
        "email",
        "message",
        "subject",
        "body",
        "token",
        "credential",
        "provider",
    ):
        assert forbidden not in serialized


def test_admin_endpoint_is_get_only_and_hidden_from_openapi() -> None:
    settings = Settings(_env_file=None, admin_enabled="true")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_reconciliation_repository] = CountingRepository
    try:
        with TestClient(app) as client:
            assert client.post("/admin/reconciliation-queue").status_code == 405
            assert "/admin/reconciliation-queue" not in client.get(
                "/openapi.json"
            ).json()["paths"]
    finally:
        app.dependency_overrides.clear()


def test_admin_database_failure_is_sanitized() -> None:
    class FailingRepository:
        def fetch_counts(self):
            raise RuntimeError("database host password customer@example.com")

    settings = Settings(_env_file=None, admin_enabled="true")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_reconciliation_repository] = FailingRepository
    try:
        with TestClient(app) as client:
            response = client.get("/admin/reconciliation-queue")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Reconciliation data is temporarily unavailable."
    }
    assert "password" not in response.text
    assert "customer@example.com" not in response.text

