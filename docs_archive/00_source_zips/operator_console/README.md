# Operator Console Design Source Archive

This directory is the canonical local archive for Operator Console design
deliveries. Future audits must resolve the latest source through `LATEST.json`
before searching ad hoc workspace paths.

## Current Source Of Truth

- Delivery: `Oday Plus 營運管理後台 (8).zip`
- Canonical copy: `r6-20260718-package-8/Oday Plus 營運管理後台 (8).zip`
- Extracted payload: `r6-20260718-package-8/extracted/`
- Package manifest: `r6-20260718-package-8/manifest.json`
- Design identity: Operator Console R6
- Demo state: `oday-plus-r6-20260718`
- ZIP SHA-256: `cacd5f3ac659e5a52be4380f469c0c20082c1dd23cd430fafd1a3a60002a97f0`
- Review disposition: `CHANGES_REQUESTED`

The user supplied a URL-encoded path. Its decoded local path is:

`/home/lupin/oday-plus/Oday Plus 營運管理後台 (8).zip`

## Package 8 Review

Package 8 is the R6 visual-design response for Assisted Listing Intake. It adds
promotion, state-matrix, role, error-recovery, and durable-receipt concepts to
the R5 intake surfaces. The package is archived as received, but its visual
design review is `CHANGES_REQUESTED`; latest does not mean approved.

See
`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW.md`
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
   the worktree and resolves to package 8 / R6.
3. Run `unzip -t` on the canonical ZIP and verify its SHA-256 against
   `LATEST.json`.
4. Open the extracted `Oday Plus Operator Console.dc.html`; do not implement
   from the prose summary alone.
5. Use the task's screen labels and acceptance assertions to capture desktop
   and constrained-width screenshots against the archived screen.
6. A reviewer must reject visual completion when the worker cannot identify the
   exact package path, screen label, and comparison evidence.

Packages 6 and 7 remain historical R4/R5 evidence. Package 8 is the latest
received source, but Fleet adoption remains blocked until the visual-design
response is resubmitted and independently approved.
