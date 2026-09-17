"""Read-only PostgreSQL access for reconciliation queue aggregates."""

from collections.abc import Mapping
from typing import Protocol

import psycopg

from app.core.config import Settings


RECONCILIATION_COUNTS_SQL = """
SELECT
  (SELECT COUNT(*) FROM leads
    WHERE initial_response_delivery_status = 'uncertain')::integer
    AS initial_response_uncertain,
  (SELECT COUNT(*) FROM leads
    WHERE followup_status = 'uncertain')::integer
    AS followup_uncertain,
  (SELECT COUNT(*) FROM flowpilot_idempotency
    WHERE status = 'recovery_required')::integer
    AS recovery_required,
  ((SELECT COUNT(*) FROM leads
      WHERE initial_response_delivery_status = 'sending'
        AND initial_response_claim_token IS NOT NULL
        AND initial_response_claimed_at IS NOT NULL
        AND initial_response_claimed_at <= NOW() - INTERVAL '30 minutes')
   +
   (SELECT COUNT(*) FROM leads
      WHERE followup_status = 'sending'
        AND followup_claimed_at IS NOT NULL
        AND followup_claimed_at <= NOW() - INTERVAL '30 minutes'))::integer
    AS stale_sending,
  (SELECT COUNT(*) FROM flowpilot_idempotency
    WHERE status = 'processing'
      AND workflow_stage = 'claimed'
      AND stage_updated_at IS NOT NULL
      AND stage_updated_at <= NOW() - INTERVAL '60 minutes')::integer
    AS stale_claimed
""".strip()


class ReconciliationRepository(Protocol):
    """Interface used by the API route and deterministic tests."""

    def fetch_counts(self) -> Mapping[str, int]:
        """Return the five allowlisted operational counts."""


class PostgresReconciliationRepository:
    """Query reconciliation aggregates without changing database state."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def fetch_counts(self) -> Mapping[str, int]:
        password = (
            self._settings.database_password.get_secret_value()
            if self._settings.database_password is not None
            else ""
        )
        with psycopg.connect(
            host=self._settings.database_host,
            port=self._settings.database_port,
            dbname=self._settings.database_name,
            user=self._settings.database_user,
            password=password,
            connect_timeout=5,
        ) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                row = connection.execute(RECONCILIATION_COUNTS_SQL).fetchone()

        if row is None or len(row) != 5:
            raise RuntimeError("Reconciliation aggregate query returned no row")
        keys = (
            "initial_response_uncertain",
            "followup_uncertain",
            "recovery_required",
            "stale_sending",
            "stale_claimed",
        )
        return dict(zip(keys, (int(value) for value in row), strict=True))
