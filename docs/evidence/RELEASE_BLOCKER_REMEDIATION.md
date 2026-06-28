---
doc_id: ODP-PV-012-RELEASE-BLOCKER-REMEDIATION
title: ODP-PV-012 Release Blocker Remediation Evidence
version: 0.1.0
status: release-candidate
owner: Codex
reviewer: Claude
updated_at: 2026-06-28
---

# ODP-PV-012 Release Blocker Remediation Evidence

## Scope

ODP-PV-012 removes production-readiness blockers identified in the PV package:

| Blocker | Remediation |
|---|---|
| Unfilled release metadata in `docs/evidence/PRODUCTION_READINESS_PACKAGE.md` | Filled task-scoped release-candidate metadata for release id, environment, build version, commit baseline, data snapshot, model applicability, feature flags, and release owner. |
| High dependency audit findings in Next.js, eslint-config-next, Playwright, transitive `glob`, and transitive `postcss` | Upgraded `next` and `eslint-config-next` to `15.5.19`; upgraded `@playwright/test` to `1.61.1`; regenerated `package-lock.json`. |
| CI did not run an explicit security/dependency gate | Added `npm run audit:security`, `make dependency-audit`, `make security`, and included `security` in `make ci`. |
| Production build was not part of the Node release gate | Migrated app routes to the Next 15 async `params`/`searchParams` contract and added `npm run build --workspaces --if-present` to `make node-check`. |
| Security gate regression was not directly asserted | Added `tests/security/test_release_security_gate.py` to verify the CI security target, high-level audit command, and production-readiness metadata are present. |

## Dependency Audit Result

Required release-blocking command:

```bash
npm audit --audit-level=high
```

Result after remediation: passed with 0 high or critical findings.

npm still reports moderate PostCSS advisories through Next.js. The production
readiness package blocks on unresolved high/critical findings; moderate findings
remain tracked by the audit output but no longer block this PV remediation.

## Security Gate

The CI path now runs:

```bash
make ci
```

`make ci` includes:

```bash
make security
```

`make security` runs the high/critical dependency audit and the checked-in
security acceptance tests under `tests/security`.

The Node release gate now runs:

```bash
make node-check
```

`make node-check` performs `npm ci`, lint, typecheck, production build, and
workspace tests when a lockfile exists. The production build step is required
because Next.js route contract validation is enforced by `next build`, not by
plain `tsc --noEmit`.

## Verification Results

Local verification on 2026-06-28:

| Command | Result |
|---|---|
| `npm audit --audit-level=high` | passed; 0 high/critical findings, 2 moderate PostCSS findings remain visible |
| `uv run pytest tests/security` | passed, 35 tests |
| `npm run lint --workspaces --if-present` | passed |
| `npm run typecheck --workspaces --if-present` | passed |
| `npm run build --workspace=@oday-plus/web` | passed; Next 15 production build generated 31 app routes |
| `make security` | passed; high audit plus 35 security tests |
| `make node-check` | passed; npm ci, lint, typecheck, Next production build, and workspace tests |
| `make ci` | passed; ruff, high audit, 35 security tests, 704 pytest tests, 2 smoke tests, npm ci, lint, typecheck, Next production build, and workspace tests |
