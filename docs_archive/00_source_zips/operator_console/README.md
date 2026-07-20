# Operator Console Design Source Archive

This directory is the canonical local archive for Operator Console design
deliveries. Future audits must resolve the latest source through `LATEST.json`
before searching ad hoc workspace paths.

## Current Source Of Truth

- Delivery: `Oday Plus 營運管理後台 (10).zip`
- Canonical copy: `r7-20260720-package-10/Oday Plus 營運管理後台 (10).zip`
- Extracted payload: `r7-20260720-package-10/extracted/`
- Package manifest: `r7-20260720-package-10/manifest.json`
- Design identity: Operator Console R7
- Canonical design tool: Claude Design
- Demo state: `oday-plus-r7-20260720`
- ZIP SHA-256: `d1583a00496f928b0765c1756c9671fedf615f12c84c00494d454c983645d7f8`
- Review disposition: `APPROVED_WITH_CONDITIONS`

The user supplied a URL-encoded path. Its decoded local path is:

`/home/lupin/oday-plus/Oday Plus 營運管理後台 (10).zip`

## Package 10 Review

Package 10 is the R7 Assisted Listing Intake resubmission and the current Claude
Design visual baseline. It closes the recurring-discovery language, exact
duplicate, canonical-code, durable intake deep-link, and source/standalone
consistency blockers. Desktop and tablet are usable, and the required mobile
intake jobs are reachable.

The approval is conditional. Engineering must apply `VDC-001` through
`VDC-005`: correct the Transfer/Pause runtime branch, remove 390 px overflow,
complete focus/contrast/landmark accessibility, serialize restorable inbox
state in the URL, and record discipline review outcomes. Where Package 10 and
the review differ, the review is binding.

See
`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`
for the binding implementation conditions and runtime evidence.

## Audit Rule

1. Decode percent-encoded paths before checking whether a supplied file exists.
2. Read `LATEST.json` and verify the archived ZIP SHA-256. Never infer the
   current package from an older task ID such as `R4-*` or `R6-*`.
3. Compare extracted file hashes, not ZIP hashes alone, when deciding whether
   the design itself changed.
4. Preserve each received package even when its internal payload is identical.
5. Read `review_disposition` and `review_conditions` before dispatching
   implementation. `APPROVED_WITH_CONDITIONS` authorizes work only when the
   conditions are carried into task acceptance and tests.

## Fleet Source Preflight

Every Operator Console implementation and review worktree must complete this
preflight before editing or approving a page:

1. Sync the task branch with the latest `origin/dev` using the repository's
   non-destructive branch policy.
2. Confirm `docs_archive/00_source_zips/operator_console/LATEST.json` exists in
   the worktree and resolves to package 10 / R7.
3. Run `unzip -t` on the canonical ZIP and verify its SHA-256 against
   `LATEST.json`.
4. Open the extracted `Oday Plus Operator Console.dc.html`; do not implement
   from the prose summary alone.
5. Use the task's screen labels and acceptance assertions to capture desktop
   and constrained-width screenshots against the archived screen.
6. A reviewer must reject visual completion when the worker cannot identify the
   exact package path, screen label, review condition, and comparison evidence.

Packages 6 and 7 remain historical R4/R5 evidence; Package 8 is the first R6
review baseline, and Package 9 is the rejected R6 resubmission. Package 10 is
the current R7 baseline for Fleet execution together with Review 003.
