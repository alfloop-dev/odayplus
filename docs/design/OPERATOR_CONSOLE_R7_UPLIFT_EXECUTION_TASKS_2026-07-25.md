# Operator Console R5→R7 Uplift + Live Data — Fleet Execution Tasks

- doc_id: ODP-OC-R7-UPLIFT-001
- date: 2026-07-25
- status: ready_for_fleet
- canonical_design: R7 / Package 10 (`docs_archive/00_source_zips/operator_console/LATEST.json`)

## Corrected premise (read first)

An earlier assessment wrongly called the React operator console
(`apps/web/features/operator/**`) "divergent" and proposed deleting it. **That was
wrong.** The repo's CI gate `scripts/e2e/check_product_grade_ci_gates.py` verifies that
**all 37 Package-7 (R5) screen labels are implemented in the React components** (PASS).
The React is the CI-verified R5 implementation, **not** garbage — do **not** delete it.

Two real facts define the remaining work:

1. The deployed operator console shows a fail-closed "OPERATOR_DATA_LOADING / API required"
   gate **because there is no authenticated live data (web→API returns 401)** — not
   because the design is wrong. With data it renders the 37-label dashboard.
2. The React is at **R5 / 37 labels**; the canonical design has advanced to
   **R7 / Package 10 / 40 labels** with approval conditions VDC-001..VDC-005. That is a
   **delta uplift**, not a rebuild.

## TASK-1 — ODP-OC-R7-AUTH-001: Close the web→API identity federation (fix the 401)

- owner_role: `Antigravity`, reviewer_role: `Claude`
- Root cause: the API auth boundary (ODP-GAP-AUTH-001, already in code) only activates when
  `ODP_AUTH_*` is set, and deployed `oday-api` has none → no verified principal → 401.
- Do: configure `ODP_AUTH_ISSUER` / `ODP_AUTH_JWKS_URI` / `ODP_AUTH_AUDIENCES`; define the
  BFF→API token + verifying header; flow the **end-user subject + roles** to
  `operator_view_guard` / `operator_write_guard`; provision operator RBAC roles; keep
  fail-closed exact (misconfig → 401, never trust spoofable `x-subject-id`).
- Accept: authenticated operator gets 200 + real data from `/api/v1/operator/bootstrap`;
  unauth 401; wrong role 403; live transcript attached. THIS is what turns the deployed
  empty gate into the real dashboard.

## TASK-2 — ODP-OC-R7-FE-DELTA-001: Uplift the existing React from R5 (37) to R7 (40) + VDC

- owner_role: `Codex`, reviewer_role: `Claude2`
- Base: the EXISTING `apps/web/features/operator/**` (do not rewrite from scratch).
- Design source of truth: `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted`
  (binding review: `ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`).
- Do: add the 3 R7 screen labels beyond R5's 37 (bring the CI gate target to Package 10 / 40),
  reconcile any R7 visual deltas, and apply VDC-001..VDC-005:
  - VDC-001 fix Transfer/Pause runtime branch
  - VDC-002 remove 390 px mobile overflow
  - VDC-003 focus/contrast/landmark accessibility
  - VDC-004 serialize restorable inbox state in the URL
  - VDC-005 record discipline review outcomes
- Do: update `check_product_grade_ci_gates.py` to target Package 10 (40 labels) once uplift lands.
- Accept: 40/40 R7 labels PASS; VDC evidence attached; a11y + mobile e2e pass; existing
  operator e2e still green.

## TASK-3 — ODP-OC-R7-DEPLOY-001: Show live data on the real stack

- owner_role: `Codex2`, reviewer_role: `Claude`
- After TASK-1: deploy `oday-api`/`oday-web` from the delta HEAD, verify `/operator` renders
  the real 40-label dashboard for an authenticated operator (no fixtures in production).

## What NOT to do

- Do **not** delete or rewrite-from-scratch `apps/web/features/operator/**`. It is the
  CI-verified R5 implementation; the task is a delta to R7, plus wiring live data.
- Do **not** treat the deployed empty gate as proof the design is wrong — it is the
  no-data fail-closed state pending TASK-1.

## Sequencing

TASK-1 (auth) unblocks everything visible. TASK-2 (R7 delta) proceeds in parallel.
TASK-3 deploys and verifies once both land.
