\set ON_ERROR_STOP on

\echo Waiting for the FlowPilot schema migration lock
SELECT pg_advisory_lock(hashtextextended('flowpilot-schema-migrations', 0));

\echo Applying FlowPilot migration 001
\ir postgres/init/001_create_leads.sql
\echo Applying FlowPilot migration 002
\ir postgres/init/002_add_lead_drafts.sql
\echo Applying FlowPilot migration 003
\ir postgres/init/003_add_human_approval.sql
\echo Applying FlowPilot migration 004
\ir postgres/init/004_add_followups.sql
\echo Applying FlowPilot migration 005
\ir postgres/init/005_add_idempotency.sql
\echo Applying FlowPilot migration 006
\ir postgres/init/006_add_email_recovery.sql
\echo Applying FlowPilot migration 007
\ir postgres/init/007_add_partial_work_reconciliation.sql
\echo Applying FlowPilot migration 008
\ir postgres/init/008_add_reliability_observability.sql
SELECT pg_advisory_unlock(hashtextextended('flowpilot-schema-migrations', 0));
\echo FlowPilot migrations complete
