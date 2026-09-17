# FlowPilot phase changelog

This is a concise milestone index derived from the repository's commit and
merge history. Detailed behavior and verification instructions are in
[PHASES.md](PHASES.md).

| Date | Phase | Verified milestone | Pull request |
| --- | --- | --- | --- |
| 2026-09-07 | Phase 1 | Added the validated AI lead-analysis API. | — |
| 2026-09-07 | Phase 2 | Added n8n webhook intake and priority routing. | #1 |
| 2026-09-08 | Phase 3A | Added PostgreSQL lead and analysis persistence. | #2 |
| 2026-09-08 | Phase 3B | Added Google Sheets persistence. | #3 |
| 2026-09-08 | Phase 4 | Added HubSpot contact synchronization. | #4 |
| 2026-09-08 | Phase 5 | Added persisted response drafts and internal notifications. | #5 |
| 2026-09-10 | Phase 6 | Added secure, explicit human approval before lead email. | #6 |
| 2026-09-10 | Phase 7 | Added one atomically claimed follow-up after 72 hours. | #7 |
| 2026-09-11 | Phase 8A | Added inbound idempotency and duplicate protection. | #8 |
| 2026-09-11 | Phase 8B1 | Added conservative email recovery and uncertain-send states. | #9 |
| 2026-09-11 | Phase 8B2 | Added partial-work stages and reconciliation. | #10 |
| 2026-09-11 | Phase 8C | Added audit events, recovery history, and operational views. | #11 |
| 2026-09-15 | Phase 9 | Added the reproducible full-stack Docker package. | #12 |
| 2026-09-16 | Phase 10A | Added OCI single-VM pilot readiness and ingress security. | #13 |
| 2026-09-17 | Final certification | Added end-to-end reliability and security certification evidence. | #14 |

No release-version scheme has been adopted. Dates above are commit dates, not
claims of production deployment or live external-provider validation.
