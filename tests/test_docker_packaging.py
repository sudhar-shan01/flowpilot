"""Credential-free static checks for the Phase 9 container package."""

from pathlib import Path
import re


ROOT = Path(__file__).parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "compose.yml").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
MIGRATION_RUNNER = (ROOT / "run-migrations.sql").read_text(encoding="utf-8")


def locked_requirements(name: str) -> dict[str, str]:
    requirements: dict[str, str] = {}
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        package, version = value.split("==", maxsplit=1)
        requirements[package.lower()] = version
    return requirements


def test_api_image_is_pinned_minimal_and_non_root() -> None:
    assert re.search(
        r"^FROM python:3\.11\.9-slim-bookworm@sha256:[0-9a-f]{64}$",
        DOCKERFILE,
        flags=re.MULTILINE,
    )
    assert "requirements.runtime.lock" in DOCKERFILE
    assert "USER flowpilot" in DOCKERFILE
    assert '"--host", "0.0.0.0"' in DOCKERFILE
    assert "--reload" not in DOCKERFILE
    assert "HEALTHCHECK" in DOCKERFILE
    assert "COPY ." not in DOCKERFILE


def test_docker_context_excludes_secrets_metadata_and_test_artifacts() -> None:
    ignored = set(DOCKERIGNORE)
    assert {".git", ".env", ".venv", "__pycache__", ".pytest_cache", "tests"} <= ignored
    assert "!.env.example" in ignored


def test_compose_defines_only_the_expected_runtime_services() -> None:
    services = COMPOSE.split("\nvolumes:\n", maxsplit=1)[0]
    service_names = set(
        re.findall(r"^  ([a-z][a-z0-9-]+):$", services, flags=re.MULTILINE)
    )
    assert service_names == {"postgres", "migrations", "flowpilot-api", "n8n"}
    assert re.search(r"n8nio/n8n:2\.37\.10@sha256:[0-9a-f]{64}", COMPOSE)
    assert COMPOSE.count("postgres:17-alpine@sha256:") == 2


def test_compose_publishes_only_api_and_n8n() -> None:
    assert '${FLOWPILOT_BIND_ADDRESS:-0.0.0.0}:${FLOWPILOT_API_PORT:-8000}:8000' in COMPOSE
    assert '${FLOWPILOT_BIND_ADDRESS:-0.0.0.0}:${FLOWPILOT_N8N_PORT:-5678}:5678' in COMPOSE
    postgres_section = COMPOSE.split("  migrations:", maxsplit=1)[0]
    assert "ports:" not in postgres_section
    assert "5432:5432" not in COMPOSE


def test_compose_uses_health_and_migration_completion_dependencies() -> None:
    assert "condition: service_healthy" in COMPOSE
    assert COMPOSE.count("condition: service_completed_successfully") == 2
    assert "sleep " not in COMPOSE.lower()
    assert "host.docker.internal" not in COMPOSE


def test_compose_uses_persistent_data_without_privileged_access() -> None:
    assert "flowpilot_postgres_data:/var/lib/postgresql/data" in COMPOSE
    assert "flowpilot_n8n_data:/home/node/.n8n" in COMPOSE
    assert "privileged:" not in COMPOSE
    assert "docker.sock" not in COMPOSE


def test_migration_runner_is_fail_fast_complete_and_ordered() -> None:
    assert "\\set ON_ERROR_STOP on" in MIGRATION_RUNNER
    assert "pg_advisory_lock" in MIGRATION_RUNNER
    assert "pg_advisory_unlock" in MIGRATION_RUNNER
    referenced = re.findall(r"postgres/init/(\d{3}_[a-z0-9_]+\.sql)", MIGRATION_RUNNER)
    committed = sorted(path.name for path in (ROOT / "postgres/init").glob("*.sql"))
    assert referenced == committed
    assert referenced == sorted(referenced)


def test_dependency_locks_are_exact_and_dev_includes_runtime() -> None:
    runtime = locked_requirements("requirements.runtime.lock")
    development = locked_requirements("requirements.dev.lock")
    assert {"fastapi", "uvicorn", "pydantic-settings", "email-validator", "httpx"} <= runtime.keys()
    assert "pytest" not in runtime
    assert development.items() >= runtime.items()
    assert development["pytest"]


def test_example_environment_is_sectioned_and_contains_placeholders_only() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for section in (
        "CORE",
        "DATABASE",
        "AI",
        "N8N",
        "EMAIL",
        "APPROVAL",
        "HUBSPOT",
        "GOOGLE SHEETS",
    ):
        assert f"# {section}" in example
    assert "POSTGRES_PASSWORD=" in example.splitlines()
    assert "N8N_ENCRYPTION_KEY=" in example.splitlines()
    assert not re.search(r"(?:sk-|gh[oprsu]_|pat_)[A-Za-z0-9_-]{12,}", example)


def test_product_docs_cover_quickstart_and_safe_failure_model() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docker compose up --build" in readme
    assert "http://localhost:8000/health" in readme
    assert "http://localhost:5678" in readme
    assert "exactly-once" in readme
    assert "docs/demo.md" in readme
    assert "docs/development-history.md" in readme


def test_postgres_credentials_distinguish_compose_from_hybrid_networking() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = readme.split("## Quick start", maxsplit=1)[1].split(
        "## Architecture", maxsplit=1
    )[0]

    assert "full-stack Compose quickstart" in quickstart
    assert "Host: `postgres`" in quickstart
    assert "Port: `5432`" in quickstart
    assert "host.docker.internal" not in quickstart
    assert "The application itself is not Dockerized." not in readme

    assert "**Full-stack Compose:**" in readme
    assert "**Legacy/hybrid mode:**" in readme
    assert "`host.docker.internal`" in readme
