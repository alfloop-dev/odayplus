# Product-Grade E2E Fleet Assignment Ledger

Generated: 2026-06-29  
Release authority: PR #82 `headRefOid` and attached checks.  
Dispatch packet: `docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH_QUEUE.json`.

## Purpose

This ledger records actual fleet pickup attempts for the product-grade E2E
execution tasks. It complements the generated dispatch briefs by naming the
worker agent, assigned lane, task ids, expected handback, and current status.

It is not completion evidence. A lane is complete only after the assigned fleet
returns implementation evidence, verification evidence, acceptance proof, and
handoff artifacts matching the task brief.

## Assignment Status

| Lane | Agent | Agent ID | Task IDs | Status | Required handback |
|---|---|---|---|---|---|
| External provider foundation | Godel | `019f1442-8505-7a41-9e13-dcee985a54fd` | `ODP-EXT-001`, `ODP-EXT-002`, `ODP-EXT-003` | handback received | repo-side tests/evidence complete; production provider credentials remain externally blocked |
| External source operations | Lovelace | `019f1442-b79e-7342-9c1a-bb6b5c190dc7` | `ODP-EXT-004`, `ODP-EXT-005`, `ODP-EXT-006`, `ODP-EXT-007`, `ODP-EXT-008` | rejected handback | worker reported changes in `/home/lupin/oday-plus`, not the release worktree `/home/lupin/odayplus-dev` |
| External source operations | Ohm | `019f1449-2c9b-72e0-bbc5-00c2123d8e42` | `ODP-EXT-004`, `ODP-EXT-005`, `ODP-EXT-006`, `ODP-EXT-007`, `ODP-EXT-008` | handback received | repo-side tests/evidence complete; production provider/license proof remains externally blocked |
| Live map provider gate | Pauli | `019f1448-498b-7e33-ab4f-9ecb4687bc1e` | `ODP-MAP-E2E-001`, `ODP-MAP-E2E-002`, `ODP-MAP-E2E-003`, `ODP-MAP-E2E-004` | rejected handback | worker reported from `/home/lupin/oday-plus`, not the release worktree `/home/lupin/odayplus-dev` |
| Live map provider gate | Huygens | `019f144b-bfe2-7290-807b-24c772305857` | `ODP-MAP-E2E-001`, `ODP-MAP-E2E-002`, `ODP-MAP-E2E-003`, `ODP-MAP-E2E-004` | handback received | repo-side map tests/evidence complete; remote-staging live endpoints remain externally blocked |
| Map accessibility and resilience | release validation | local validation | `ODP-MAP-A11Y-001`, `ODP-MAP-E2E-005`, `ODP-MAP-E2E-006` | covered by existing product evidence | specs already exist in PR #82; rerun in this validation pass |
| Remote staging rollout | Plato | `019f1452-62af-7850-a8dd-a52226369adf` | `ODP-PV-STAGE-001`, `ODP-PV-STAGE-002` | handback received; externally blocked | fail-closed checker evidence captured; staging host/url/secrets/drill are absent |

## Dispatch Instructions Sent

### Godel

- Read `docs/evidence/fleet_dispatch/ODP-EXT-001.md`,
  `docs/evidence/fleet_dispatch/ODP-EXT-002.md`, and
  `docs/evidence/fleet_dispatch/ODP-EXT-003.md`.
- Own external provider registry/secrets, live listing adapter, and live
  geocoder adapter repo-side work.
- Run brief execution commands.
- Report changed files, commands, completion status, and remaining
  provider-specific credential/OAuth blockers.

Handback received:

- Changed files:
  - `tests/e2e/test_external_source_product_e2e.py`
  - `tests/data/test_geo_pipeline.py`
  - `docs/evidence/fleet_dispatch/ODP-EXT-001-003_WORKER_EVIDENCE.md`
- Verified command: `uv run pytest tests/e2e/test_external_source_product_e2e.py tests/data/test_geo_pipeline.py -q`
- Local result: `15 passed`
- Remaining blocker: provider-specific production credentials/OAuth, production
  listing snapshots, and production geocoder responses are absent.

### Lovelace

- Read `docs/evidence/fleet_dispatch/ODP-EXT-004.md` through
  `docs/evidence/fleet_dispatch/ODP-EXT-008.md`.
- Own scheduled fetch, quota/rate-limit resilience, freshness/data-quality,
  licensing, and external source product E2E repo-side work.
- Run brief execution commands.
- Report changed files, commands, completion status, and remaining
  provider/license/secret blockers.

Handback rejected:

- The returned file paths pointed at `/home/lupin/oday-plus`, whose `main`
  checkout is behind `origin/main` by 127 commits and contains unrelated dirty
  orchestrator changes.
- No Lovelace changes are accepted as release evidence for `/home/lupin/odayplus-dev`.
- The lane was re-dispatched to Ohm with the correct worktree path.

### Ohm

- Read `docs/evidence/fleet_dispatch/ODP-EXT-004.md` through
  `docs/evidence/fleet_dispatch/ODP-EXT-008.md` in `/home/lupin/odayplus-dev`.
- Own scheduled fetch, quota/rate-limit resilience, freshness/data-quality,
  licensing, and external source product E2E repo-side work.
- Run brief execution commands in `/home/lupin/odayplus-dev`.
- Report changed files, commands, completion status, and remaining
  provider/license/secret blockers.

Handback received:

- Changed files:
  - `tests/e2e/test_external_source_product_e2e.py`
  - `docs/evidence/fleet_dispatch/ODP-EXT-004-008_WORKER_EVIDENCE.md`
- Verified command: `uv run pytest tests/e2e/test_external_source_product_e2e.py tests/data/test_geo_pipeline.py -q`
- Local result after integrating Godel and Ohm changes: `17 passed`
- Remaining blocker: real provider secrets/live credentials, third-party
  production licensing approval, and provider-specific production proof are
  absent.

### Pauli

- Read `docs/evidence/fleet_dispatch/ODP-MAP-E2E-001.md` through
  `docs/evidence/fleet_dispatch/ODP-MAP-E2E-004.md`.
- Own HeatZone map live tile/geocoder boundary, layer URL persistence, direct
  map picking, and deck semantic pixel proof.
- Run brief execution commands.
- Report changed files, commands, completion status, and remaining
  remote-staging live tile/geocoder endpoint blockers.

Handback rejected:

- The returned file paths pointed at `/home/lupin/oday-plus`, not the release
  worktree `/home/lupin/odayplus-dev`.
- The worker reported missing map files that exist in `/home/lupin/odayplus-dev`.
- No Pauli changes are accepted as release evidence for PR #82.

### Huygens

- Preflight must run in `/home/lupin/odayplus-dev` and verify:
- `apps/web/features/expansion`
- `tests/e2e/e2e-map.spec.ts`
- `docs/evidence/fleet_dispatch/ODP-MAP-E2E-001.md`
- Own HeatZone map live tile/geocoder boundary, layer URL persistence, direct
  map picking, and deck semantic pixel proof in the release worktree.
- Report changed files, commands, completion status, and remaining
  remote-staging live tile/geocoder endpoint blockers.

Handback received:

- Changed file:
  - `docs/evidence/fleet_dispatch/ODP-MAP-E2E-001-004_WORKER_EVIDENCE.md`
- Verified commands:
  - `npx playwright test tests/e2e/e2e-map-live-boundary.spec.ts --project=chromium --retries=1`
  - `npx playwright test tests/e2e/e2e-map.spec.ts --project=chromium --retries=1`
- Local results: `3 passed` and `5 passed`
- Remaining blocker: remote-staging live tile/geocoder endpoint smoke and PR
  #82 release-SHA staging evidence are absent.

### Map accessibility and resilience

These tasks were already implemented in the PR #82 product evidence packet.
They are retained in the dispatch queue because they are part of the map proof
surface, but they do not require a fresh fleet handback in this pass.

Validation rerun:

- `npx playwright test tests/e2e/e2e-map-a11y.spec.ts --project=chromium --retries=1`
- `npx playwright test tests/e2e/e2e-map-resilience.spec.ts tests/e2e/e2e-map-tooltip-evidence.spec.ts --project=chromium --retries=1`
- Local results: `2 passed` and `6 passed`

Covered tasks:

- `ODP-MAP-A11Y-001`: keyboard selection, layer controls, drawer close focus
  return, and axe scan.
- `ODP-MAP-E2E-005`: loading, empty, no-geometry, error correlation id,
  partial layer failure, and list/detail fallback.
- `ODP-MAP-E2E-006`: hover tooltip, keyboard reachable tooltip, evidence
  fields, and fallback text.

### Plato

- Preflight passed in `/home/lupin/odayplus-dev` for:
  - `docs/evidence/fleet_dispatch/ODP-PV-STAGE-001.md`
  - `docs/evidence/fleet_dispatch/ODP-PV-STAGE-002.md`
  - `scripts/e2e/check_remote_staging_proof.py`
- Reviewed `REMOTE_STAGING_PROOF_RUNBOOK.md`.
- Checked GitHub repo/environment inventory without printing secret values.
- Ran the remote staging checker with PR #82 `headRefOid`.

Handback received:

- Changed files:
  - `docs/evidence/fleet_dispatch/ODP-PV-STAGE-001-002_WORKER_EVIDENCE.md`
  - `docs/evidence/fleet_dispatch/ODP-PV-STAGE-001_MISSING_ENV_REPORT.json`
- Checker result: expected fail-closed exit `1`.
- Missing env: `ODP_STAGING_DEPLOY_URL`, `ODP_STAGING_API_URL`,
  `ODP_STAGING_SECRET_OWNER`.
- Remaining blocker: real staging host/deployment target, release-SHA injection,
  remote health/version proof, staging product smoke, and backup/restore/
  rollback drill evidence are absent.

Do not close `ODP-PV-STAGE-001` or `ODP-PV-STAGE-002` from this evidence. It
is blocker evidence only.

## Completion Rules

- Do not mark any dispatched task complete from this ledger alone.
- Do not claim provider-specific production credentials, remote-staging live
  map rollout, or remote staging drill completion without runtime evidence.
- Do not commit provider secrets.
- Release evidence must use PR #82 `headRefOid`, not a hardcoded dev hash.
