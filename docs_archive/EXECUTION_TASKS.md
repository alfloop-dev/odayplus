# Execution Tasks Archive

Task: ODP-PV-000
Generated: 2026-06-28

## Current Task Baseline

ODP-PV-000 produced the current-state evidence baseline for the PV
Product-Grade E2E Validation phase.

Artifacts:

- `docs/evidence/BRANCH_TRUTH_TABLE.md`
- `docs/evidence/CURRENT_STATE_PRODUCT_GAP_AUDIT.md`
- `docs_archive/EXECUTION_TASKS.md`

## Follow-On Task Seeds

| Suggested task | Purpose | Depends on |
|---|---|---|
| Durable repository wiring | Replace in-memory module repositories with database-backed implementations while preserving domain/application boundaries | branch truth + product gap audit |
| Fixture-to-API web binding | Convert OpsBoard workspaces from bundled `data.ts` fixtures to API-backed state one module at a time | durable API contracts |
| External ingestion productionization | Implement POI, competitor, and listing source acquisition, quarantine, and freshness evidence | source contract registry |
| Map/geocoder readiness | Replace HeatZone preview map and static geocoder path with production map, geocoder, H3/PostGIS, and licensing controls | external ingestion + geo pipeline |
| Evidence store and audit retention | Persist audit events and evidence export bundles with hashes, privacy scope, and retention controls | audit export baseline |
| Release blocker remediation | Fill release metadata, remediate dependency audit findings, and generate deploy/UAT evidence | production readiness package |

## Notes

This archive entry is intentionally task-scoped. It does not replace the
canonical execution baseline or RTM; it records the concrete follow-on work
revealed by the ODP-PV-000 audit.
