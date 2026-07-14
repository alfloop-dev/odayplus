# Operator Console Design Source Archive

This directory is the canonical local archive for Operator Console design
deliveries. Future audits must resolve the latest source through `LATEST.json`
before searching ad hoc workspace paths.

## Current Source Of Truth

- Delivery: `Oday Plus 營運管理後台 (6).zip`
- Canonical copy: `r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip`
- Extracted payload: `r4-20260707-package-6/extracted/`
- Package manifest: `r4-20260707-package-6/manifest.json`
- Design identity: Operator Console R4
- Demo state: `oday-plus-r4-20260707`
- ZIP SHA-256: `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76`

The user supplied a URL-encoded path. Its decoded local path is:

`/home/lupin/oday-plus/Oday Plus 營運管理後台 (6).zip`

## Package 6 Versus Package 5

The ZIP hashes differ because all five entry timestamps changed from
2026-07-07 01:43 to 2026-07-13 14:26. The five extracted files are otherwise
byte-identical. Package 6 therefore changes delivery provenance but introduces
zero design-screen or implementation-scope changes.

See `docs/evidence/OPERATOR_CONSOLE_DESIGN_PACKAGE_6_DIFF_2026-07-13.md` for
the complete screen-by-screen receipt.

## Audit Rule

1. Decode percent-encoded paths before checking whether a supplied file exists.
2. Read `LATEST.json` and verify the archived ZIP SHA-256.
3. Compare extracted file hashes, not ZIP hashes alone, when deciding whether
   the design itself changed.
4. Preserve each received package even when its internal payload is identical.

## Fleet Source Preflight

Every Operator Console implementation and review worktree must complete this
preflight before editing or approving a page:

1. Sync the task branch with the latest `origin/dev` using the repository's
   non-destructive branch policy.
2. Confirm `docs_archive/00_source_zips/operator_console/LATEST.json` exists in
   the worktree and resolves to package 6.
3. Run `unzip -t` on the canonical ZIP and verify its SHA-256 against
   `LATEST.json`.
4. Open the extracted `Oday Plus Operator Console.dc.html`; do not implement
   from the prose summary alone.
5. Use the task's screen labels and acceptance assertions to capture desktop
   and constrained-width screenshots against the archived screen.
6. A reviewer must reject visual completion when the worker cannot identify the
   exact package path, screen label, and comparison evidence.

Published to `dev` by `ODP-OC-R4-014` so independent Fleet worktrees receive
the same source bytes and instructions.
