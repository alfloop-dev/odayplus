---
doc_id: ODP-R7-003-PRODUCTION-READINESS-PACKAGE
title: ODay Plus Production Readiness Package
version: 0.1.0
status: draft
owner: Release Owner / QA Lead / SRE Owner / Security Owner
source_documents:
  - ODP-QA-01_TEST_MASTER_PLAN.md
  - ODP-QA-03_END_TO_END_TEST_SCENARIOS.md
  - ODP-QA-05_PERFORMANCE_SECURITY_AND_DR_TEST.md
  - ODP-QA-06_UAT_AND_FORMAL_ACCEPTANCE_CHECKLIST.md
  - ODP-QA-07_SUBSIDY_AUDIT_EVIDENCE_MATRIX.md
---

# ODay Plus Production Readiness Package

## Release Metadata

| Field | Value |
|---|---|
| Release ID | `[ASSIGNMENT_REQUIRED]` |
| Environment | staging / production |
| Build version | `[ASSIGNMENT_REQUIRED]` |
| Git commit | `[ASSIGNMENT_REQUIRED]` |
| Data snapshot | `[ASSIGNMENT_REQUIRED]` |
| Model versions | `[ASSIGNMENT_REQUIRED_OR_NA]` |
| Feature flags | `[ASSIGNMENT_REQUIRED_OR_NA]` |
| Release owner | `[ASSIGNMENT_REQUIRED]` |
| Evidence package version | `0.1.0` |

## Required Evidence Manifest

Each evidence item must include:

| Field | Required |
|---|---|
| evidence_id | yes |
| evidence_type | yes |
| related_requirement | yes |
| module | yes |
| environment | yes |
| build_version | yes |
| data_snapshot | yes |
| model_version | when model-backed |
| generated_at | yes |
| generated_by | yes |
| file_path | yes |
| hash | yes |
| reviewed_by | yes |
| status | yes |

Allowed evidence types: `SCREENSHOT`, `VIDEO`, `API_RESPONSE`, `JOB_LOG`,
`TEST_REPORT`, `MODEL_REPORT`, `DATA_REPORT`, `AUDIT_EXPORT`, `UAT_SIGNOFF`,
`SECURITY_REPORT`, `DR_REPORT`, `SLA_REPORT`, `DEPLOYMENT_RECORD`.

## Release Gate Packet

| Gate | Required command or artifact | Blocking condition |
|---|---|---|
| Code Gate | `npm run lint`, `npm run typecheck`, `npm test` where configured | Any failing changed-scope check |
| E2E Gate | `npm run test:e2e` plus `tests/e2e/test_acceptance_coverage.py` | Any P0 E2E lacks automation, data, or audit evidence |
| Data Gate | Contract, canonical mapping, freshness, lineage, PIT, and backfill reports | Schema/freshness/PIT failure |
| Model Gate | Model card, baseline, holdout, calibration, segment metrics, rollback target | Failed model validity or missing approval |
| Performance Gate | API P95, frontend render, batch/job, solver runtime report | Target exceeds QA-05 budget |
| Security Gate | RBAC/ABAC, privacy/export, CI scans, OWASP API cases | Unresolved critical/high finding |
| Reliability/DR Gate | Backup restore and DR drill report with RPO/RTO | RPO > 1 hour or RTO > 4 hours |
| UAT Gate | Completed role sign-offs from `docs/uat/UAT_ACCEPTANCE_PLAN.md` | P0/P1 UAT defect or missing high-risk sign-off |
| Audit Gate | Evidence manifest and decision audit exports | Missing hash, missing actor/reason, or untraceable decision |

## Subsidy Evidence Coverage

| QA-07 ID | Module | Required evidence |
|---|---|---|
| AUD-MOD-001 | SiteScore | report screenshot, API response, model report, UAT sign-off |
| AUD-MOD-002 | ForecastOps | alert center screenshot, forecast report, E2E report |
| AUD-MOD-003 | DealRoomAVM | valuation card, AVM report, finance approval audit |
| AUD-MOD-004 | NetPlan | scenario result, solver log, alternative plan screenshot |
| AUD-MOD-005 | OpsBoard | dashboard screenshot, decision log export, monthly report |
| AUD-MOD-006 | Integration / Data | data lineage, mapping report, data quality report |
| AUD-MOD-007 | Learning Hub | model registry, model card, release approval |

## Readiness Decision

Production readiness is approved only when every release gate is `passed` or
`passed-with-deviation` with an owner, due date, and explicit approval. An open
P0/P1 defect, unresolved high/critical security finding, failed DR target,
missing audit export, or missing high-risk UAT sign-off blocks production.

## ODP-R7-003 Verification Baseline

| Check | Command | Result |
|---|---|---|
| Acceptance/security/performance registry | `uv run pytest tests/e2e/test_acceptance_coverage.py tests/performance tests/security` | passed, 39 tests |
| OpsBoard E2E smoke | `npm run test:e2e` | passed, 29 tests |
| Dependency security audit | `npm audit --audit-level=high` | failed: 6 high and 1 moderate findings |

The dependency audit findings are release blockers until remediated or formally
accepted with compensating controls by the Security Owner. The reported fix path
requires major or out-of-range dependency upgrades for Next.js and Playwright,
so it is intentionally not applied inside this acceptance-suite task.
