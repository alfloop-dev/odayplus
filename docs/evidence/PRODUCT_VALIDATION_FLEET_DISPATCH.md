# Product Validation Fleet Dispatch

Task: ODP-PV-000
Generated: 2026-06-28
Owner: Codex
Reviewer: Claude

## Purpose

This dispatch note turns the ODP-PV-000 current-state audit into bounded
follow-on lanes for the PV Product-Grade E2E Validation phase. It is an
evidence artifact, not a claim that the product is production-ready.

## Dispatch Baseline

The reviewed baseline is:

- `docs/evidence/BRANCH_TRUTH_TABLE.md`
- `docs/evidence/CURRENT_STATE_PRODUCT_GAP_AUDIT.md`
- `docs_archive/EXECUTION_TASKS.md`

The baseline found that `task/ODP-PV-000`, `origin/main`, and `origin/dev`
have identical product trees, while the task branch still needs the normal PR
flow into `dev` because branch topology differs.

## Fleet Lanes

| Lane | Objective | Starting evidence | Done signal |
|---|---|---|---|
| Durable persistence | Replace default in-memory API/module repositories with durable stores behind existing interfaces | `CURRENT_STATE_PRODUCT_GAP_AUDIT.md` P0 repository wiring gap | API and module tests prove persisted state survives process boundaries |
| Fixture-to-API web binding | Move OpsBoard screens from bundled `data.ts` fixtures to API-backed state incrementally | Audit web data binding gap | E2E scenarios exercise live API contracts for converted screens |
| Evidence store and audit retention | Persist audit events and evidence export bundles with hash, retention, and privacy scope | Audit evidence durability gap | Exported evidence can be reproduced from durable storage with recorded hashes |
| External ingestion | Implement provider-backed POI, competitor, and listing ingestion with quarantine and freshness checks | Source contract registry and audit external data gap | Scheduled or manual ingest produces validated snapshots and freshness evidence |
| Map/geocoder readiness | Replace HeatZone preview behavior with production map, geocoder, and spatial lookup path | Branch audit geospatial/map findings | UI renders licensed map/geocoder results backed by approved spatial data |
| Model registry integration | Connect model lifecycle code to artifact storage, model aliases, and release evidence | Audit model lifecycle gap | A signed model card references durable artifacts and rollback metadata |
| Release blocker remediation | Resolve dependency/security findings and fill release metadata before any release-ready claim | `PRODUCTION_READINESS_PACKAGE.md` blockers | Release package has assigned owners, versions, approvals, and passing checks |

## Coordination Notes

- Fleet tasks should treat the current repo as a demo-capable modular product
  skeleton.
- Follow-on work must not claim live external data, production map readiness,
  durable audit retention, formal UAT closure, or release readiness until each
  lane produces its own evidence.
- Cross-lane sequencing should prioritize durable persistence and API contracts
  before broad fixture-to-API screen conversion.
- Any release-readiness task must cite the unresolved blockers in
  `docs/evidence/PRODUCTION_READINESS_PACKAGE.md` before changing approval
  language.

## Closeout Scope

This file reconciles the ODP-PV-000 declared artifact list. It does not change
runtime code, canonical architecture policy, task ownership, or release status.
