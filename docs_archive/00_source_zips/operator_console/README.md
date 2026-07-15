# Operator Console Design Source Archive

This directory is the canonical local archive for Operator Console design
deliveries. Future audits must resolve the latest source through `LATEST.json`
before searching ad hoc workspace paths.

## Current Source Of Truth

- Delivery: `Oday Plus 營運管理後台 (7).zip`
- Canonical copy: `r5-20260715-package-7/Oday Plus 營運管理後台 (7).zip`
- Extracted payload: `r5-20260715-package-7/extracted/`
- Package manifest: `r5-20260715-package-7/manifest.json`
- Design identity: Operator Console R5
- Demo state: `oday-plus-r5-20260714`
- ZIP SHA-256: `fa1a980d1d0c3fe2102e11ac009a57a1fe25bdb5539f9bd03378c2a628a9b552`

The user supplied a URL-encoded path. Its decoded local path is:

`/home/lupin/oday-plus/Oday Plus 營運管理後台 (7).zip`

## Package 7 Versus Package 6

Package 7 is a material R5 design change. It preserves all 32 R4 screen labels
and adds five assisted-listing surfaces: URL intake queue, add-URL dialog,
processing detail, field correction, and decision confirmation. `oday-map.js`
and `support.js` remain byte-identical; the interactive HTML, summary, and
thumbnail changed.

See `docs/evidence/OPERATOR_CONSOLE_DESIGN_PACKAGE_7_DIFF_2026-07-15.md` for
the complete screen-by-screen receipt.

## Audit Rule

1. Decode percent-encoded paths before checking whether a supplied file exists.
2. Read `LATEST.json` and verify the archived ZIP SHA-256. Never infer the
   current package from an older task ID such as `R4-*`.
3. Compare extracted file hashes, not ZIP hashes alone, when deciding whether
   the design itself changed.
4. Preserve each received package even when its internal payload is identical.

## Fleet Source Preflight

Every Operator Console implementation and review worktree must complete this
preflight before editing or approving a page:

1. Sync the task branch with the latest `origin/dev` using the repository's
   non-destructive branch policy.
2. Confirm `docs_archive/00_source_zips/operator_console/LATEST.json` exists in
   the worktree and resolves to package 7 / R5.
3. Run `unzip -t` on the canonical ZIP and verify its SHA-256 against
   `LATEST.json`.
4. Open the extracted `Oday Plus Operator Console.dc.html`; do not implement
   from the prose summary alone.
5. Use the task's screen labels and acceptance assertions to capture desktop
   and constrained-width screenshots against the archived screen.
6. A reviewer must reject visual completion when the worker cannot identify the
   exact package path, screen label, and comparison evidence.

Package 6 remains historical R4 evidence. Package 7 supersedes it for every
new implementation, validation, and release decision. Publication and Fleet
adoption are tracked by `ODP-OC-R5-000` and the R5 execution task pack.
