"""Static and CLI validation for the Phase 10A production deployment layer."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[1]
BASE = (ROOT / "compose.yml").read_text(encoding="utf-8")
PROD = (ROOT / "compose.prod.yml").read_text(encoding="utf-8")
CADDY = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
PROD_ENV = (ROOT / ".env.prod.example").read_text(encoding="utf-8")


def test_production_compose_requires_security_critical_configuration() -> None:
    for variable in (
        "FLOWPILOT_WEBHOOK_INGRESS_SECRET",
        "FLOWPILOT_PUBLIC_HOST",
        "FLOWPILOT_PUBLIC_ORIGIN",
        "FLOWPILOT_CADDY_SITE",
    ):
        assert f"${{{variable}:?" in PROD
    assert "${POSTGRES_PASSWORD:?" in BASE
    assert "${N8N_ENCRYPTION_KEY:?" in BASE


def test_production_network_exposes_only_caddy_publicly() -> None:
    assert re.search(r"caddy:2\.10\.2-alpine@sha256:[0-9a-f]{64}", PROD)
    assert "FLOWPILOT_BIND_ADDRESS:-0.0.0.0" in BASE
    assert '"${FLOWPILOT_HTTP_PORT:-80}:80"' in PROD
    assert '"${FLOWPILOT_HTTPS_PORT:-443}:443"' in PROD
    assert "5432:5432" not in BASE + PROD
    assert re.search(r"backend:\n    internal: true", BASE)
    caddy_section = PROD.split("  caddy:", maxsplit=1)[1].split(
        "\nvolumes:", maxsplit=1
    )[0]
    assert "- edge" in caddy_section
    assert "- backend" not in caddy_section
    assert "docker.sock" not in BASE + PROD
    assert "privileged:" not in BASE + PROD


def test_caddy_publishes_only_exact_safe_routes() -> None:
    assert "@health path /health" in CADDY
    assert "@lead path /webhook/flowpilot/lead" in CADDY
    assert "@approval path /webhook/flowpilot/approval" in CADDY
    assert "uri /internal/ingress/verify" in CADDY
    assert "header_up -X-FlowPilot-Webhook-Secret" in CADDY
    assert CADDY.count("forward_auth") == 1
    assert 'respond "Not found" 404' in CADDY
    assert "max_size 64KB" in CADDY
    assert "max_size 16KB" in CADDY
    assert "no-referrer" in CADDY
    assert "\n\tlog" not in CADDY
    assert "/admin" not in CADDY


def test_n8n_production_privacy_and_retention_are_bounded() -> None:
    assert "EXECUTIONS_DATA_SAVE_ON_SUCCESS: none" in PROD
    assert "EXECUTIONS_DATA_SAVE_ON_ERROR: all" in PROD
    assert 'EXECUTIONS_DATA_SAVE_ON_PROGRESS: "false"' in PROD
    assert 'EXECUTIONS_DATA_PRUNE: "true"' in PROD
    assert "EXECUTIONS_DATA_MAX_AGE" in PROD
    assert "N8N_EDITOR_BASE_URL: http://localhost:" in PROD
    assert "N8N_PROTOCOL: ${FLOWPILOT_PUBLIC_PROTOCOL:-https}" in PROD
    assert "WEBHOOK_URL: ${FLOWPILOT_PUBLIC_ORIGIN" in PROD


def test_production_example_contains_only_placeholders() -> None:
    for name in (
        "FLOWPILOT_CADDY_SITE",
        "FLOWPILOT_PUBLIC_HOST",
        "FLOWPILOT_PUBLIC_ORIGIN",
        "FLOWPILOT_WEBHOOK_INGRESS_SECRET",
        "POSTGRES_PASSWORD",
        "OPENAI_API_KEY",
        "N8N_ENCRYPTION_KEY",
    ):
        assert re.search(rf"^{name}=$", PROD_ENV, re.MULTILINE)
    assert not re.search(r"(?:sk-|gh[oprsu]_|pat_)[A-Za-z0-9_-]{12,}", PROD_ENV)
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env.*" in ignored
    assert "!.env.prod.example" in ignored


def test_oci_runbook_covers_full_pilot_lifecycle_and_boundaries() -> None:
    runbook = (ROOT / "docs" / "oci-deployment.md").read_text(encoding="utf-8")
    for heading in (
        "Prerequisites",
        "VM recommendation",
        "Always Free and capacity caveats",
        "ARM64 image compatibility",
        "OCI network and firewall paths",
        "Production environment and secrets",
        "Start and verify",
        "n8n first-time setup",
        "Backup",
        "Restore",
        "Update",
        "Rollback",
        "Single-VM limitations",
        "Pilot client boundary",
    ):
        assert f"## {heading}" in runbook
    assert "docker compose down -v" in runbook
    assert "DESTRUCTIVE" in runbook
    assert "single point of failure" in runbook
    assert "exactly-once" in runbook
    assert "Phase 10 does not build" in runbook
    assert "VM.Standard.A1.Flex" in runbook
    assert "Ubuntu 24.04" in runbook
    assert "linux/arm64" in runbook
    assert "reserved public IPv4" in runbook
    assert "5432" in runbook and "5678" in runbook and "8000" in runbook
    for removed_reference in (
        "Google Compute Engine",
        "GCE",
        "IAP",
        "GCP project",
        "Google Secret Manager",
        "gcp-deployment.md",
    ):
        assert removed_reference not in runbook
    assert not (ROOT / "docs" / "gcp-deployment.md").exists()


def test_production_compose_renders_with_synthetic_configuration(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI unavailable; static deployment checks still ran")
    env_file = tmp_path / "phase10.env"
    env_file.write_text(
        "\n".join(
            (
                "POSTGRES_PASSWORD=synthetic-postgres-password",
                "N8N_ENCRYPTION_KEY=synthetic-n8n-encryption-key-32-bytes",
                "FLOWPILOT_WEBHOOK_INGRESS_SECRET=synthetic-webhook-secret-32-bytes",
                "FLOWPILOT_PUBLIC_HOST=flowpilot.example.test",
                "FLOWPILOT_PUBLIC_ORIGIN=https://flowpilot.example.test",
                "FLOWPILOT_CADDY_SITE=flowpilot.example.test",
                "FLOWPILOT_BIND_ADDRESS=127.0.0.1",
            )
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            docker,
            "compose",
            "--project-name",
            "flowpilot-phase10-config-test",
            "--env-file",
            str(env_file),
            "--file",
            "compose.yml",
            "--file",
            "compose.prod.yml",
            "config",
            "--quiet",
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
