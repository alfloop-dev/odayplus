# Operator Console R7 (Package 10) Rebuild — Fleet Execution Tasks

- doc_id: ODP-OC-R7-REBUILD-001
- date: 2026-07-25
- status: ready_for_fleet
- canonical_design: R7 / Package 10 (`docs_archive/00_source_zips/operator_console/LATEST.json`, APPROVED_WITH_CONDITIONS)
- canonical_extracted: `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted`

## Context (read first)

The prior React operator console (`apps/web/features/operator/**`) is the CI-verified
R5/37-label implementation; the owner decided (2026-07-25) to rebuild from the canonical
**R7 / Package 10** design (40 labels) rather than carry it forward
(see `OPERATOR_CONSOLE_REACT_RETIREMENT_2026-07-25.md`). It is **not deleted yet**: the
operator feature is a load-bearing dependency of `shell` and `expansion`
(`operatorNetworkClient`, `AssistedIntakeSection`, `OperatorRoleId`), so retiring it must be
done as part of this rebuild in a build-green way (preserve those shared modules or refactor
the dependents) — not a naive delete that ships a red PR. Build the R7 console from the
Package 10 design; do not carry forward the R5 console UI.

## Backend is ready

`oday-api` is live on durable Postgres with three live external providers
(geocode/POI/admin-boundary). The `/api/v1/operator/*` routes exist and are served
(`apps/api/app/routes/operator.py`). Remaining runtime gap: web→API identity federation
(TASK-2).

## TASK-1 — ODP-OC-R7-FE-001: Build the Operator Console from R7/Package 10, API-connected

- owner_role: `Codex`, reviewer_role: `Claude2`, scope: `apps/web/**`
- Design source of truth: Package 10 extracted interactive HTML (40 screen labels). Where
  Package 10 and the R7 review differ, the review is binding
  (`ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`).
- Implement every R7 region: shell + top nav (今日工作 / 門市營運 / 營收成長 / 展店與店網 /
  治理稽核), Today KPI row + issue queue + decision cards, Store Ops, Growth, Network
  (Find Areas / Candidate / SiteScore / Review / Rebalance), Govern, and the Assisted
  Listing Intake flow (URL submit → identity → source policy → assisted entry → parsed/
  normalized/corrected review → NEW / EXACT_DUPLICATE / REVISION / POSSIBLE_MATCH /
  QUARANTINED → decision confirm).
- Bind to real data via the BFF: `/api/v1/operator/bootstrap`, `/shell`, `/approvals`,
  plus intake/network endpoints. **No mock/seed/fixture in production**; fail-closed empty
  states allowed, fabricated data not.
- Add a new R7 label-parity gate (40 labels) to replace the retired R5 gate.
- Accept: 40/40 R7 labels present with screenshot parity to Package 10; production renders
  live API data for an authenticated operator (depends on TASK-2); Playwright e2e covers the
  R7 shell + intake happy path against api+web.

## TASK-2 — ODP-OC-R7-AUTH-001: Close the web→API identity federation (fix the 401)

- owner_role: `Antigravity`, reviewer_role: `Claude`
- Root cause: the API auth boundary (ODP-GAP-AUTH-001, already in code) only activates when
  `ODP_AUTH_*` is set, and deployed `oday-api` has none → no verified principal → 401.
- Do: configure `ODP_AUTH_ISSUER` / `ODP_AUTH_JWKS_URI` / `ODP_AUTH_AUDIENCES`; define the
  BFF→API token + verifying header; flow the end-user subject + roles to
  `operator_view_guard` / `operator_write_guard`; provision operator RBAC roles; keep
  fail-closed exact (misconfig → 401, never trust spoofable `x-subject-id`).
- Accept: authenticated operator gets 200 + real data from `/api/v1/operator/bootstrap`;
  unauth 401; wrong role 403; live transcript attached.

## TASK-3 — ODP-OC-R7-VDC-001: Apply the R7 approval conditions VDC-001..VDC-005

- owner_role: `Codex2`, reviewer_role: `Claude2`, folds into TASK-1.
- VDC-001 Transfer/Pause runtime branch; VDC-002 remove 390 px mobile overflow;
  VDC-003 focus/contrast/landmark a11y; VDC-004 serialize restorable inbox state in URL;
  VDC-005 record discipline review outcomes. Each condition has runtime evidence.

## Sequencing

TASK-2 (auth) and TASK-1 (frontend) proceed in parallel; TASK-1 cannot show live data until
TASK-2 lands. TASK-3 folds into TASK-1. Do not restore the retired React.
