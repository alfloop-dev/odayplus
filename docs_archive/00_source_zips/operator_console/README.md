# Operator Console Design Source Archive

This directory is the canonical local archive for Operator Console design
deliveries. Future audits must resolve the latest source through `LATEST.json`
before searching ad hoc workspace paths.

## Current Source Of Truth

- Delivery: `Oday Plus 營運管理後台 (9).zip`
- Canonical copy: `r6-20260719-package-9/Oday Plus 營運管理後台 (9).zip`
- Extracted payload: `r6-20260719-package-9/extracted/`
- Package manifest: `r6-20260719-package-9/manifest.json`
- Design identity: Operator Console R6
- Demo state: `oday-plus-r6-20260718`
- ZIP SHA-256: `601a55b29f1097c6c50938f30e1acbdf4c9dc7f1ff9dfbc07021b00ac6f12abd`
- Review disposition: `CHANGES_REQUESTED`

The user supplied a URL-encoded path. Its decoded local path is:

`/home/lupin/oday-plus/Oday Plus 營運管理後台 (9).zip`

## Package 9 Review

Package 9 is the R6 Assisted Listing Intake resubmission. It closes the exact
duplicate identity/stage defect and canonical-code defect, and adds partial
assignment, evidence, dialog, hash-link, and mobile-fallback changes. The
package remains `CHANGES_REQUESTED`: source scanning language, required
responsive workflows, accessibility, durable submission routing, Figma/review
evidence, Pause/evidence behavior, and package consistency remain unresolved.

The standalone R6 HTML in Package 9 is byte-identical to Package 8 and does not
contain the canonical prototype corrections. Do not use it as implementation
evidence.

See
`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_002.md`
for the binding review and remediation requirements.

## Audit Rule

1. Decode percent-encoded paths before checking whether a supplied file exists.
2. Read `LATEST.json` and verify the archived ZIP SHA-256. Never infer the
   current package from an older task ID such as `R4-*`.
3. Compare extracted file hashes, not ZIP hashes alone, when deciding whether
   the design itself changed.
4. Preserve each received package even when its internal payload is identical.
5. Read `review_disposition` before dispatching implementation. A latest package
   with `CHANGES_REQUESTED` is review evidence, not an implementation baseline.

## Fleet Source Preflight

Every Operator Console implementation and review worktree must complete this
preflight before editing or approving a page:

1. Sync the task branch with the latest `origin/dev` using the repository's
   non-destructive branch policy.
2. Confirm `docs_archive/00_source_zips/operator_console/LATEST.json` exists in
   the worktree and resolves to package 9 / R6.
3. Run `unzip -t` on the canonical ZIP and verify its SHA-256 against
   `LATEST.json`.
4. Open the extracted `Oday Plus Operator Console.dc.html`; do not implement
   from the prose summary alone.
5. Use the task's screen labels and acceptance assertions to capture desktop
   and constrained-width screenshots against the archived screen.
6. A reviewer must reject visual completion when the worker cannot identify the
   exact package path, screen label, and comparison evidence.

Packages 6 and 7 remain historical R4/R5 evidence; Package 8 is the first R6
review baseline. Package 9 is the latest received source, but Fleet adoption
remains blocked until the visual-design response is resubmitted and
independently approved.
